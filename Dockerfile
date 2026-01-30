FROM ubuntu:22.04

LABEL maintainer="Harishwar Reddy Kasireddy - Sarder Lab. <harishwarreddy.k@ufl.edu>"

RUN echo !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! STARTING THE BUILD !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!


RUN apt-get update && \
    apt-get install --yes --no-install-recommends software-properties-common gnupg && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get autoremove && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get --yes --no-install-recommends -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" dist-upgrade && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    #keyboard-configuration \
    git \
    wget \
    python3-pyqt5 \
    curl \
    ca-certificates \
    libcurl4-openssl-dev \
    libexpat1-dev \
    unzip \
    libhdf5-dev \
    libpython3-dev \
    # python2.7-dev \
    # python-tk \
    # We can't go higher than 3.7 and use tensorflow 1.x \
    python3.9-dev \
    python3.9-distutils \
    python3-tk \
    software-properties-common \
    libssl-dev \
    # Standard build tools \
    build-essential \
    cmake \
    autoconf \
    automake \
    libtool \
    pkg-config \
    # needed for supporting CUDA \
    # libcupti-dev \
    # useful later \
    libmemcached-dev && \
    #apt-get autoremove && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN echo !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! CHECKPOINT !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

RUN apt-get update ##[edited]
RUN apt-get install 'ffmpeg'\
    'libsm6'\
    'libxext6'  -y

RUN apt-get install libxml2-dev libxslt1-dev -y

WORKDIR /
# Make Python3 the default and install pip.  Whichever is done last determines
# the default python version for pip.

#Make a specific version of python the default and install pip
RUN rm -f /usr/bin/python /usr/bin/python3 && \
    ln -sf $(which python3.9) /usr/bin/python && \
    ln -sf $(which python3.9) /usr/bin/python3 && \
    curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py && \
    python3.9 get-pip.py && \
    rm get-pip.py && \
    ln -sf $(which pip3) /usr/bin/pip

RUN which  python && \
    python --version

ENV build_path=/opt/build
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python

ENV ig_path=/opt/Tubule_Lumen_Filter_Plugin
RUN mkdir -p $ig_path

RUN apt-get update && \
    apt-get install -y --no-install-recommends memcached && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY . $ig_path/
WORKDIR $ig_path

# Upgrade setuptools, as the version in Conda won't upgrade cleanly unless it
# is ignored.

RUN pip install --no-cache-dir --upgrade --ignore-installed pip "setuptools<81" && \
    pip install --no-cache-dir .  && \
    rm -rf /root/.cache/pip/*

# Show what was installed
RUN python --version && pip --version && pip freeze
# Define entrypoint through which all CLIs can be run
WORKDIR $ig_path/Refine_Tubular_SCSeg/cli
RUN chmod +x $ig_path/Refine_Tubular_SCSeg/cli/docker-entrypoint.sh
LABEL entry_path=$ig_path/Refine_Tubular_SCSeg/cli

# Test our entrypoint.  If we have incompatible versions of numpy and
# Openslide, one of these will fail
RUN python -m slicer_cli_web.cli_list_entrypoint --list_cli
RUN python -m slicer_cli_web.cli_list_entrypoint Refining_Subcompartment_segmentation --help

ENTRYPOINT ["/bin/bash", "docker-entrypoint.sh"] 