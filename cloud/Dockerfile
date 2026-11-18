# 어느 호스트든 컨테이너로 띄울 때 (Render/Railway/Koyeb/Fly 등은 자동 인식)
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scraper.py server.py index.html ./
ENV PORT=8790
EXPOSE 8790
CMD ["python", "server.py"]
