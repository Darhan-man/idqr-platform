from app.main import flask_app

if __name__ == '__main__':
    print("=" * 50)
    print("🚀 Запуск приложения из run.py")
    print("📍 Адрес: http://127.0.0.1:5000")
    print("=" * 50)
    flask_app.run(host='0.0.0.0', port=5000, debug=True)
