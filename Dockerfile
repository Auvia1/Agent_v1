# Use the official lightweight Python 3.12 Linux image
FROM python:3.12-slim

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies required for audio processing
RUN apt-get update && apt-get install -y \
    build-essential \
    ffmpeg \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# 🛑 INSTALL CPU-ONLY PYTORCH FIRST (Tiny, downloads in seconds)
RUN pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install the rest of your dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Expose the port
EXPOSE 8000

# Command to run your app
CMD ["python", "main.py"]