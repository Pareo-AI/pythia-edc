# Multi-stage build for Pythia EDC Demo Environment
FROM eclipse-temurin:17-jdk AS edc-builder

# Install git for cloning EDC samples
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Clone EDC Samples repository
WORKDIR /build
RUN git clone https://github.com/eclipse-edc/Samples.git edc-samples

# Build the required connector JARs
WORKDIR /build/edc-samples
RUN ./gradlew :transfer:transfer-00-prerequisites:connector:build
RUN ./gradlew :transfer:transfer-03-consumer-pull:provider-proxy-data-plane:build

# Main runtime stage
FROM eclipse-temurin:17-jdk

# Install Python 3, curl, lsof
RUN apt-get update && \
    apt-get install -y python3 python3-pip python3-venv curl lsof && \
    rm -rf /var/lib/apt/lists/*

# Install uv for Python dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy EDC samples from builder stage
COPY --from=edc-builder /build/edc-samples /app/edc-samples

# Set EDC_SAMPLES_DIR environment variable for start_demo.sh
ENV EDC_SAMPLES_DIR=/app/edc-samples

# Copy only dependency files first for better layer caching
COPY pyproject.toml uv.lock* README.md ./

# Install Python dependencies (will be cached unless pyproject.toml or uv.lock changes)
RUN uv sync --all-extras

# Copy the rest of the project files
COPY . /app

# Make scripts executable
RUN chmod +x /app/scripts/*.sh

# Default command (can be overridden by docker-compose)
CMD ["/bin/bash", "/app/scripts/start_demo.sh"]
