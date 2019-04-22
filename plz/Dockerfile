FROM amazonlinux:2
RUN yum install -y https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm
RUN yum install -y git gcc python3-pip python3-devel && yum clean all
RUN export PYTHONPATH=~/deps && python3 -m pip install --upgrade pip
ENV HOME /root/
