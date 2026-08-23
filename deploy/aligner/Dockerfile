# HTTP wrapper around NUS AutoLyrixAlign.
#
# The base image ships the AutoLyrixAlign model + Kaldi. We add only a
# stdlib-Python HTTP server that shells out to RunAlignment.sh, so this stays a
# separate *process* the MIT-licensed app talks to over HTTP — it never links
# against or vendors the GPLv3 engine.
#
# Build & run (on the host that has the RAM — alignment peaks near 13 GB):
#   docker build -t xonset-aligner deploy/aligner
#   docker run -d --name xonset-aligner -p 3001:3001 xonset-aligner
#   curl localhost:3001/health
FROM helvio/karaokeer

COPY align_server.py /usr/local/bin/align_server.py

ENV ALIGN_DIR=/NUSAutoLyrixAlign \
    ALIGN_PORT=3001
EXPOSE 3001

# Override the base image's karaoke web app — we only want the aligner.
ENTRYPOINT ["python3", "/usr/local/bin/align_server.py"]
CMD []
