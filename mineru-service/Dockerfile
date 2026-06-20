FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev \
    libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# Install magic-pdf + fastapi + pymupdf
RUN pip install --no-cache-dir \
    magic-pdf \
    fastapi \
    uvicorn \
    python-multipart \
    pydantic \
    PyMuPDF

# magic-pdf config
RUN mkdir -p /root && echo '{}' > /root/magic-pdf.json

COPY server.py /app/server.py

EXPOSE 8001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
