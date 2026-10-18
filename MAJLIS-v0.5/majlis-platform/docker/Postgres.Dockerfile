FROM postgres:17-alpine
COPY initdb/10-app.sh /docker-entrypoint-initdb.d/10-app.sh
RUN chmod 0644 /docker-entrypoint-initdb.d/10-app.sh
