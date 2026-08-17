FROM python:3.12-slim

# cairosvg needs the cairo runtime. DejaVu is installed so that a Raspberry Pi
# renders the same metrics your laptop does — cairo resolves font families
# against whatever the host happens to have, and silently substituting a
# different face shifts every text baseline in the poster.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY flightframe ./flightframe

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    OUT_DIR=/out \
    CACHE_DIR=/cache

VOLUME ["/data", "/out", "/cache"]

ENTRYPOINT ["python", "-m", "flightframe.cli"]
CMD ["where"]
