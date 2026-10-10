FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html styles.css app.js /usr/share/nginx/html/
COPY vendor /usr/share/nginx/html/vendor
RUN chmod -R a+rX /usr/share/nginx/html
