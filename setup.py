from setuptools import find_namespace_packages, setup
from codecs import open


with open('README.md', 'r', 'utf-8') as f:
    readme = f.read()


with open('VERSION', 'r', 'utf-8') as f:
    version = f.read()


setup(
    name='prodinf-reporting',
    version=version,
    description='',
    long_description=readme,
    packages=find_namespace_packages(include=['ensembl.*']),
    license='Apache 2.0',
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        'elasticsearch6',
        'kombu',
    ],
    python_requires='>=3.10, <4',
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: POSIX",
        "Operating System :: Unix",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Utilities",
        "Topic :: System :: Distributed Computing",

    ],
)
