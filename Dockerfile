FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY static ./static
COPY data ./data
COPY presets ./presets
EXPOSE 8765
CMD ["python","-m","uvicorn","backend.server:app","--host","0.0.0.0","--port","8765"]
