# Backup sidecar: the same Postgres 16 client tools as the database, rclone for the off-site copy
# (any S3-compatible store: Cloudflare R2, Backblaze B2, AWS S3) and age for optional encryption.
FROM postgres:16-alpine
RUN apk add --no-cache rclone age tzdata
COPY common.sh backup-now.sh backup-loop.sh restore.sh drill.sh list.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/*.sh
CMD ["backup-loop.sh"]
