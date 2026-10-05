FROM python:3.13-slim
WORKDIR /app
COPY * ./
RUN pip install -r requirements.txt
EXPOSE 8000
CMD ["fastapi", "run", "acs_data.py", "--port=8000"]
