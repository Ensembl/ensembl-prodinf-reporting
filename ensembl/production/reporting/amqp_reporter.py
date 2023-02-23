#    See the NOTICE file distributed with this work for additional information
#    regarding copyright ownership.
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#        http://www.apache.org/licenses/LICENSE-2.0
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
import json
import logging
import signal
import ssl
import sys
import urllib3
from contextlib import contextmanager
from elasticsearch import Elasticsearch, ElasticsearchException
from elasticsearch.connection import create_ssl_context
from email.message import EmailMessage
from kombu import Connection, Queue, Consumer, Message
from kombu.asynchronous import Hub
from smtplib import SMTP, SMTPException
from typing import Any

from ensembl.production.reporting.config import config

LOG_LEVEL = logging.DEBUG if config.debug else logging.INFO
logging.basicConfig(
    stream=sys.stdout,
    format="%(asctime)s %(levelname)-8s %(name)-15s: %(message)s",
    level=LOG_LEVEL,
)

logger = logging.getLogger("amqp_reporter")


queue = Queue(config.amqp_queue)
AMQP_URI = f"amqp://{config.amqp_user}:{config.amqp_pass}@{config.amqp_host}:{config.amqp_port}/{config.amqp_virtual_host}"
conn = Connection(AMQP_URI)
hub = Hub()


def validate_payload(message_body: Any) -> dict:
    try:
        payload = json.loads(message_body)
    except json.JSONDecodeError as err:
        msg = f"Cannot decode JSON message. {err}."
        raise ValueError(msg) from err
    if not isinstance(payload, dict):
        msg = f"Invalid message type: JSON message must be of type 'object'."
        raise ValueError(msg)
    return payload


@contextmanager
def es_reporter():
    urllib3.disable_warnings(category=urllib3.connectionpool.InsecureRequestWarning)
    ssl_context = create_ssl_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    es = Elasticsearch(hosts=[{'host': config.es_host,'port': config.es_port}],
                       scheme=config.es_protocol,
                       ssl_context=ssl_context,
                       http_auth=(config.es_user, config.es_password))
    if not es.ping():
        logger.error(
            "Cannot connect to Elasticsearch server. Host: %s, Port: %s",
            config.es_host,
            config.es_port,
        )

    def on_message(message: Message):
        logger.debug("From queue: %s, received: %s", config.amqp_queue, message.body)
        try:
            validate_payload(message.body)
        except ValueError as err:
            logger.error("%s Message: %s", err, message.body)
            message.reject()
            logger.warning("Rejected: %s", message.body)
            return
        try:
            es.index(
                index=config.es_index, body=message.body, doc_type=config.es_doc_type
            )
        except ElasticsearchException as err:
            logger.error("Cannot modify index %s. Error: %s", config.es_index, err)
            message.reject()
            logger.warning("Rejected: %s", message.body)
            return
        logger.debug(
            "To index: %s, type: %s, document: %s",
            config.es_index,
            config.es_doc_type,
            message.body,
        )
        message.ack()
        logger.debug("Acked: %s", message.body)

    try:
        yield on_message
    finally:
        try:
            es.close()
        except AttributeError:
            pass


def compose_email(email: dict) -> EmailMessage:
    msg = EmailMessage()
    try:
        msg["Subject"] = email["subject"]
        msg["From"] = config.smtp_user
        msg["To"] = email["to"]  # This can be a list of str
        msg.set_content(email["content"])
    except KeyError as err:
        raise ValueError(f"Cannot parse message. Invalid key: {err}.")
    return msg


@contextmanager
def smtp_reporter():
    try:
        with SMTP(host=config.smtp_host, port=config.smtp_port) as smtp:
            smtp.noop()
    except (ConnectionRefusedError, SMTPException) as err:
        logger.error(
            "Cannot connect to SMTP server: %s Host: %s, Port: %s",
            err,
            config.smtp_host,
            config.smtp_port,
        )

    def on_message(message: Message):
        logger.debug("From queue: %s, received: %s", config.amqp_queue, message.body)
        try:
            email = validate_payload(message.body)
            msg = compose_email(email)
        except ValueError as err:
            logger.error("%s Email Message: %s", err, email)
            message.reject()
            logger.warning("Rejected: %s", message.body)
            return
        try:
            with SMTP(host=config.smtp_host, port=config.smtp_port) as smtp:
                smtp.starttls()  #TODO: We should check server's certificate here.
                smtp.login(config.smtp_user, config.smtp_pass)
                smtp.send_message(msg)
        except (ConnectionRefusedError, SMTPException) as err:
            logger.error("Cannot send email message: %s Message: %s", err, email)
            message.reject()
            logger.warning("Rejected: %s", message.body)
            return
        logger.info("Email sent: %s", email)
        message.ack()
        logger.debug("Acked: %s", message.body)

    yield on_message


def stop_gracefully():
    hub.close()
    conn.release()
    hub.stop()


def sigint_handler(_signum, _frame):
    logger.info("Received SIGINT. Terminating.")
    stop_gracefully()


def sigterm_handler(_signum, _frame):
    logger.info("Received SIGTERM. Terminating.")
    stop_gracefully()


def main():
    signal.signal(signal.SIGINT, sigint_handler)
    signal.signal(signal.SIGTERM, sigterm_handler)
    try:
        conn.register_with_event_loop(hub)
    except ConnectionRefusedError as err:
        logger.critical("Cannot connect to %s: %s", AMQP_URI, err)
        logger.critical("Exiting.")
        sys.exit(1)

    logger.info("Configuration: %s", config)
    if config.reporter_type == "elasticsearch":
        report = es_reporter()
    elif config.reporter_type == "email":
        report = smtp_reporter()
    with report as on_message_report:
        with Consumer(
            conn,
            [queue],
            prefetch_count=config.amqp_prefetch,
            on_message=on_message_report,
            auto_declare=False
        ):
            logger.info("Starting main loop")
            hub.run_forever()


if __name__ == "__main__":
    main()
