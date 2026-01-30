#! /usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path

from setuptools import find_packages
from setuptools import setup

def read_long_description():
    for candidate in ("README.rst", "README.md"):
        p = Path(__file__).with_name(candidate)
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "Package description."

long_description = read_long_description()

def prerelease_local_scheme(version):
    """
    Return local scheme version unless building on master in CircleCI.

    This function returns the local scheme version number
    (e.g. 0.0.0.dev<N>+g<HASH>) unless building on CircleCI for a
    pre-release in which case it ignores the hash and produces a
    PEP440 compliant pre-release version number (e.g. 0.0.0.dev<N>).
    """
    from setuptools_scm.version import get_local_node_and_date

    if os.getenv('CIRCLE_BRANCH') in {'master'}:
        return ''
    else:
        return get_local_node_and_date(version)


setup(
    name='podo',
    use_scm_version={'local_scheme': prerelease_local_scheme},
    description='A Python toolkit for Histopathology Image Analysis',
    long_description=long_description,
    long_description_content_type='text/x-rst',
    author='Harishwar Reddy Kasireddy',
    author_email='harishwarreddy.k@ufl.edu',
    url='https://github.com/SarderLab/Tubule_Lumen_Filter_Plugin',
    packages=find_packages(exclude=['tests', '*_test']),
    include_package_data=True,
    install_requires = [
        "numpy>=1.26,<2.0",
        "scipy>=1.13,<2.0",
        "Pillow>=11.0.0,<12",
        "opencv-python>=4.7",
        "scikit-image>=0.24",
        "tiffslide>=2.0",
        "tqdm>=4.66",
        "girder-slicer-cli-web",
        "girder-client",
        "ctk-cli",
        "lxml>=5.0",          # 4.8.0 does NOT support Python 3.12; use 5.x+
        "joblib>=1.3",
        "openpyxl>=3.1.5",
        "xlrd<2.0.0",             # keep if you still rely on old .xls parsing
    ],

    license='Apache Software License 2.0',
    keywords='SC_seg',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Topic :: Scientific/Engineering :: Artificial Intelligence',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    zip_safe=False,
)