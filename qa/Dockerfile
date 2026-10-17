# Compatibility harness only. Container scores are not provider benchmark evidence.
FROM ubuntu:24.04
COPY shapehost-agent-0.2.0.tar.gz /tmp/agent-release.tar.gz
RUN mkdir /tmp/release && tar -xzf /tmp/agent-release.tar.gz -C /tmp/release \
 && cd /tmp/release/agent && ./install.sh \
 && shapehost-agent --version \
 && test -z "$(pg_lsclusters --no-header)" \
 && useradd --uid 10001 --create-home --shell /bin/bash benchmark \
 && rm -rf /tmp/release /tmp/agent-release.tar.gz /var/lib/apt/lists/*
USER 10001:10001
ENV HOME=/home/benchmark
WORKDIR /home/benchmark
CMD ["shapehost-agent", "run", "--dry-run"]
