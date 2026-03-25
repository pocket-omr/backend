FROM python:3.12

WORKDIR /code

# # Install system deps (needed for psycopg2, OpenCV later)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     libpq-dev gcc \
#     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY . /code

CMD ["fastapi", "run", "app/main.py", "--port", "80"]