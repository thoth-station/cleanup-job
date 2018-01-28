FROM fedora:27
CMD ["/app.py"]

COPY ./app.py /
COPY ./requirements.txt /
RUN pip3 install -r requirements.txt
