FROM python:3.11-slim
WORKDIR /ui
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY .streamlit ./.streamlit
COPY . .
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 FEEDBACK_FILE=/ui/state/feedback.jsonl
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
