"""Gunicorn entry point: gunicorn -b 127.0.0.1:9797 wsgi:app"""
from itc import create_app

app = create_app()
