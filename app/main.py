from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, Response
import sqlite3
import hashlib
import os
import json
from datetime import datetime, timedelta
import secrets
from functools import wraps
import csv
import io
from werkzeug.utils import secure_filename
import qrcode
from io import BytesIO
import base64
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import uuid
import traceback
from urllib.parse import urlparse, urljoin

# Добавь эту часть В САМОМ НАЧАЛЕ
from pathlib import Path

# Получаем абсолютный путь к корневой папке проекта
BASE_DIR = Path(__file__).resolve().parent.parent

# Исправление для ASGI/WSGI совместимости
try:
    from asgiref.wsgi import WsgiToAsgi
    ASGI_COMPATIBLE = True
except ImportError:
    ASGI_COMPATIBLE = False

# СОЗДАЕМ APP С ПРАВИЛЬНЫМ ПУТЕМ К TEMPLATES
app = Flask(__name__, 
            template_folder=str(BASE_DIR / 'templates'),
            static_folder=str(BASE_DIR / 'static') if (BASE_DIR / 'static').exists() else None)

app.secret_key = secrets.token_hex(32)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['UPLOAD_FOLDER'] = str(BASE_DIR / 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
app.config['DATABASE'] = str(BASE_DIR / 'idqr_system.db')

# Конфигурация базы данных для использования в функциях
DATABASE_PATH = str(BASE_DIR / 'idqr_system.db')

# ==================== УТИЛИТЫ БАЗЫ ДАННЫХ ====================

def get_db_connection():
    """Создание соединения с базой данных"""
    # Используем DATABASE_PATH вместо app.config
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Инициализация базы данных с созданием всех таблиц"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            full_name VARCHAR(100),
            phone VARCHAR(20),
            company VARCHAR(100),
            position VARCHAR(100),
            role VARCHAR(20) DEFAULT 'user',
            status VARCHAR(20) DEFAULT 'active',
            theme VARCHAR(10) DEFAULT 'light',
            language VARCHAR(10) DEFAULT 'ru',
            avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP,
            last_activity TIMESTAMP,
            login_count INTEGER DEFAULT 0,
            settings TEXT DEFAULT '{}',
            api_key VARCHAR(64) UNIQUE,
            two_factor_enabled BOOLEAN DEFAULT 0,
            email_verified BOOLEAN DEFAULT 0,
            phone_verified BOOLEAN DEFAULT 0,
            verification_token VARCHAR(64),
            reset_token VARCHAR(64),
            reset_expires TIMESTAMP
        )
    ''')
    
    # Таблица сессий
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id VARCHAR(64) UNIQUE NOT NULL,
            ip_address VARCHAR(45),
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    
    # Таблица активности
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action_type VARCHAR(50) NOT NULL,
            module VARCHAR(50),
            description TEXT,
            ip_address VARCHAR(45),
            user_agent TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    
    # Таблица модулей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) NOT NULL,
            code VARCHAR(50) UNIQUE NOT NULL,
            description TEXT,
            icon VARCHAR(50),
            category VARCHAR(50),
            version VARCHAR(20),
            author VARCHAR(100),
            enabled BOOLEAN DEFAULT 1,
            settings TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица разрешений модулей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS module_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module_id INTEGER NOT NULL,
            role VARCHAR(50) NOT NULL,
            can_view BOOLEAN DEFAULT 1,
            can_edit BOOLEAN DEFAULT 0,
            can_delete BOOLEAN DEFAULT 0,
            can_manage BOOLEAN DEFAULT 0,
            FOREIGN KEY (module_id) REFERENCES modules(id) ON DELETE CASCADE
        )
    ''')
    
    # Таблица доступа пользователей к модулям
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_module_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            module_id INTEGER NOT NULL,
            access_level VARCHAR(20) DEFAULT 'view',
            granted_by INTEGER,
            granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (module_id) REFERENCES modules(id) ON DELETE CASCADE,
            UNIQUE(user_id, module_id)
        )
    ''')
    
    # Таблица настроек системы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key VARCHAR(100) UNIQUE NOT NULL,
            setting_value TEXT,
            setting_type VARCHAR(20) DEFAULT 'string',
            category VARCHAR(50),
            description TEXT,
            is_public BOOLEAN DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица уведомлений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title VARCHAR(200) NOT NULL,
            message TEXT NOT NULL,
            notification_type VARCHAR(50),
            icon VARCHAR(50),
            is_read BOOLEAN DEFAULT 0,
            action_url TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    
    # Таблица документов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            filename VARCHAR(255),
            file_path TEXT,
            file_type VARCHAR(50),
            file_size INTEGER,
            mime_type VARCHAR(100),
            category VARCHAR(50),
            tags TEXT,
            is_public BOOLEAN DEFAULT 0,
            is_encrypted BOOLEAN DEFAULT 0,
            version INTEGER DEFAULT 1,
            parent_id INTEGER,
            downloads INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    
    # Таблица аудита
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action VARCHAR(100) NOT NULL,
            entity_type VARCHAR(50),
            entity_id INTEGER,
            old_values TEXT,
            new_values TEXT,
            ip_address VARCHAR(45),
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица API ключей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            api_key VARCHAR(64) UNIQUE NOT NULL,
            name VARCHAR(100),
            permissions TEXT,
            last_used TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    
    # Таблица категорий модулей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS module_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) NOT NULL,
            code VARCHAR(50) UNIQUE NOT NULL,
            description TEXT,
            icon VARCHAR(50),
            color VARCHAR(20),
            sort_order INTEGER DEFAULT 0,
            parent_id INTEGER,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parent_id) REFERENCES module_categories(id) ON DELETE SET NULL
        )
    ''')
    
    # Таблица файлов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            stored_filename VARCHAR(255) UNIQUE NOT NULL,
            file_path TEXT NOT NULL,
            file_type VARCHAR(50),
            file_size INTEGER,
            mime_type VARCHAR(100),
            is_public BOOLEAN DEFAULT 0,
            downloads INTEGER DEFAULT 0,
            upload_ip VARCHAR(45),
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    
    # Таблица логов ошибок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            error_type VARCHAR(100),
            error_message TEXT,
            stack_trace TEXT,
            request_url TEXT,
            request_method VARCHAR(10),
            request_data TEXT,
            ip_address VARCHAR(45),
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица статистики
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE UNIQUE NOT NULL,
            total_users INTEGER DEFAULT 0,
            active_users INTEGER DEFAULT 0,
            new_users INTEGER DEFAULT 0,
            total_logins INTEGER DEFAULT 0,
            total_requests INTEGER DEFAULT 0,
            storage_used INTEGER DEFAULT 0,
            documents_created INTEGER DEFAULT 0,
            modules_used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица языков
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS languages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(10) UNIQUE NOT NULL,
            name VARCHAR(50) NOT NULL,
            native_name VARCHAR(50),
            is_active BOOLEAN DEFAULT 1,
            is_default BOOLEAN DEFAULT 0,
            sort_order INTEGER DEFAULT 0
        )
    ''')
    
    # Таблица переводов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            language_code VARCHAR(10) NOT NULL,
            key VARCHAR(200) NOT NULL,
            value TEXT NOT NULL,
            context VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(language_code, key)
        )
    ''')
    
    conn.commit()
    
    # Создаем администратора по умолчанию
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if cursor.fetchone() is None:
        password_hash = hashlib.sha256('admin123'.encode()).hexdigest()
        cursor.execute('''
            INSERT INTO users (username, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', ('admin', 'admin@idqr.com', password_hash, 'Администратор Системы', 'admin', 'active'))
    
    # Добавляем стандартные модули
    default_modules = [
        ('Энергетика и инфраструктура', 'energy', 'Управление энергетическими объектами и инфраструктурой', 'bolt', 'infrastructure'),
        ('Медицина и здоровье', 'medicine', 'Медицинские услуги и управление здоровьем', 'heartbeat', 'health'),
        ('Бизнес и магазины', 'business', 'Управление бизнесом и торговыми точками', 'store', 'business'),
        ('Образование', 'education', 'Образовательные платформы и курсы', 'graduation-cap', 'education'),
        ('Транспорт и авто', 'transport', 'Транспортные системы и автомобили', 'car', 'transport'),
        ('Строительство', 'construction', 'Строительные проекты и объекты', 'hard-hat', 'construction'),
        ('ЖКХ и дома', 'housing', 'Жилищно-коммунальное хозяйство', 'home', 'housing'),
        ('Безопасность', 'security', 'Системы безопасности и контроля', 'shield-alt', 'security'),
        ('Документы', 'documents', 'Управление документами и файлами', 'file-alt', 'documents'),
        ('Одежда и мода', 'clothing', 'Одежда и мода', 'tshirt', 'retail'),
        ('Услуги и быт', 'services', 'Услуги и быт', 'tools', 'services'),
        ('Склад и логистика', 'logistics', 'Склад и логистика', 'shipping-fast', 'logistics'),
        ('События и вход', 'events', 'События и вход', 'ticket-alt', 'events'),
        ('Документы и удостоверения', 'docs', 'Документы и удостоверения', 'file-alt', 'documents'),
        ('Госуслуги и учёт', 'gov', 'Госуслуги и учёт', 'landmark', 'government'),
        ('Реклама и аналитика', 'ads', 'Реклама и аналитика', 'ad', 'marketing'),
        ('Курсы и тренинги', 'courses', 'Курсы и тренинги', 'book-open', 'education'),
        ('Подарки и сервис', 'gifts', 'Подарки и сервис', 'gift', 'services'),
        ('Маркетинг и бренды', 'branding', 'Маркетинг и бренды', 'tag', 'marketing'),
        ('Квитанции и оплата', 'payment', 'Квитанции и оплата', 'receipt', 'finance')
    ]
    
    for name, code, description, icon, category in default_modules:
        cursor.execute("SELECT id FROM modules WHERE code = ?", (code,))
        if cursor.fetchone() is None:
            cursor.execute('''
                INSERT INTO modules (name, code, description, icon, category, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, code, description, icon, category, 1))
    
    # Добавляем настройки системы по умолчанию
    default_settings = [
        ('site_name', 'IDQR Система', 'string', 'general', 'Название сайта'),
        ('site_description', 'Комплексная система управления', 'string', 'general', 'Описание сайта'),
        ('site_url', 'http://localhost:5000', 'string', 'general', 'URL сайта'),
        ('admin_email', 'admin@idqr.com', 'string', 'general', 'Email администратора'),
        ('registration_enabled', 'true', 'boolean', 'auth', 'Разрешить регистрацию'),
        ('email_verification', 'false', 'boolean', 'auth', 'Требовать подтверждение email'),
        ('default_theme', 'light', 'string', 'ui', 'Тема по умолчанию'),
        ('items_per_page', '20', 'number', 'ui', 'Элементов на странице'),
        ('maintenance_mode', 'false', 'boolean', 'system', 'Режим обслуживания'),
        ('backup_enabled', 'true', 'boolean', 'system', 'Автоматическое резервное копирование'),
        ('storage_limit', '1073741824', 'number', 'storage', 'Лимит хранилища (в байтах)'),
        ('file_size_limit', '52428800', 'number', 'storage', 'Максимальный размер файла'),
        ('allowed_file_types', 'jpg,jpeg,png,gif,pdf,doc,docx,xls,xlsx,zip', 'string', 'storage', 'Разрешенные типы файлов')
    ]
    
    for key, value, type_, category, description in default_settings:
        cursor.execute("SELECT id FROM system_settings WHERE setting_key = ?", (key,))
        if cursor.fetchone() is None:
            cursor.execute('''
                INSERT INTO system_settings (setting_key, setting_value, setting_type, category, description)
                VALUES (?, ?, ?, ?, ?)
            ''', (key, value, type_, category, description))
    
    # Добавляем языки
    languages = [
        ('ru', 'Русский', 'Русский', 1, 1),
        ('en', 'English', 'English', 1, 0),
        ('kz', 'Қазақша', 'Қазақша', 1, 0)
    ]
    
    for code, name, native_name, is_active, is_default in languages:
        cursor.execute("SELECT id FROM languages WHERE code = ?", (code,))
        if cursor.fetchone() is None:
            cursor.execute('''
                INSERT INTO languages (code, name, native_name, is_active, is_default)
                VALUES (?, ?, ?, ?, ?)
            ''', (code, name, native_name, is_active, is_default))
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def hash_password(password):
    """Хеширование пароля"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, password_hash):
    """Проверка пароля"""
    return hash_password(password) == password_hash

def generate_api_key():
    """Генерация API ключа"""
    return secrets.token_hex(32)

def generate_verification_token():
    """Генерация токена верификации"""
    return secrets.token_hex(32)

def generate_reset_token():
    """Генерация токена сброса пароля"""
    return secrets.token_hex(32)

def log_activity(user_id, action_type, module=None, description=None, metadata=None):
    """Логирование активности пользователя"""
    conn = get_db_connection()
    try:
        ip_address = request.remote_addr if request else '127.0.0.1'
        user_agent = request.user_agent.string if request and request.user_agent else ''
        
        if metadata and not isinstance(metadata, str):
            metadata = json.dumps(metadata)
        
        conn.execute('''
            INSERT INTO user_activity (user_id, action_type, module, description, ip_address, user_agent, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, action_type, module, description, ip_address, user_agent, metadata))
        
        # Обновляем время последней активности пользователя
        conn.execute('UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE id = ?', (user_id,))
        
        conn.commit()
    except Exception as e:
        print(f"Ошибка при логировании активности: {e}")
    finally:
        conn.close()

def log_error(user_id=None, error_type=None, error_message=None, stack_trace=None):
    """Логирование ошибок"""
    conn = get_db_connection()
    try:
        request_url = request.url if request else None
        request_method = request.method if request else None
        request_data = json.dumps(request.form.to_dict()) if request and request.form else None
        ip_address = request.remote_addr if request else '127.0.0.1'
        user_agent = request.user_agent.string if request and request.user_agent else ''
        
        conn.execute('''
            INSERT INTO error_logs (user_id, error_type, error_message, stack_trace, request_url, request_method, request_data, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, error_type, error_message, stack_trace, request_url, request_method, request_data, ip_address, user_agent))
        
        conn.commit()
    except Exception as e:
        print(f"Ошибка при логировании ошибки: {e}")
    finally:
        conn.close()

def audit_log(user_id, action, entity_type=None, entity_id=None, old_values=None, new_values=None):
    """Логирование действий для аудита"""
    conn = get_db_connection()
    try:
        ip_address = request.remote_addr if request else '127.0.0.1'
        user_agent = request.user_agent.string if request and request.user_agent else ''
        
        if old_values and not isinstance(old_values, str):
            old_values = json.dumps(old_values)
        
        if new_values and not isinstance(new_values, str):
            new_values = json.dumps(new_values)
        
        conn.execute('''
            INSERT INTO audit_log (user_id, action, entity_type, entity_id, old_values, new_values, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, action, entity_type, entity_id, old_values, new_values, ip_address, user_agent))
        
        conn.commit()
    except Exception as e:
        print(f"Ошибка при аудите: {e}")
    finally:
        conn.close()

def create_notification(user_id, title, message, notification_type=None, icon=None, action_url=None, metadata=None):
    """Создание уведомления для пользователя"""
    conn = get_db_connection()
    try:
        if metadata and not isinstance(metadata, str):
            metadata = json.dumps(metadata)
        
        conn.execute('''
            INSERT INTO notifications (user_id, title, message, notification_type, icon, action_url, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, title, message, notification_type, icon, action_url, metadata))
        
        conn.commit()
    except Exception as e:
        print(f"Ошибка при создании уведомления: {e}")
    finally:
        conn.close()

def get_user_notifications(user_id, limit=10, unread_only=False):
    """Получение уведомлений пользователя"""
    conn = get_db_connection()
    try:
        query = 'SELECT * FROM notifications WHERE user_id = ?'
        params = [user_id]
        
        if unread_only:
            query += ' AND is_read = 0'
        
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        
        notifications = conn.execute(query, params).fetchall()
        return [dict(notification) for notification in notifications]
    except Exception as e:
        print(f"Ошибка при получении уведомлений: {e}")
        return []
    finally:
        conn.close()

def get_system_setting(key, default=None):
    """Получение настройки системы"""
    conn = get_db_connection()
    try:
        result = conn.execute('SELECT setting_value FROM system_settings WHERE setting_key = ?', (key,)).fetchone()
        if result:
            return result['setting_value']
        return default
    except Exception as e:
        print(f"Ошибка при получении настройки: {e}")
        return default
    finally:
        conn.close()

def update_system_setting(key, value):
    """Обновление настройки системы"""
    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT OR REPLACE INTO system_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (key, value))
        conn.commit()
        return True
    except Exception as e:
        print(f"Ошибка при обновлении настройки: {e}")
        return False
    finally:
        conn.close()

def get_user_by_id(user_id):
    """Получение пользователя по ID"""
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        return dict(user) if user else None
    except Exception as e:
        print(f"Ошибка при получении пользователя: {e}")
        return None
    finally:
        conn.close()

def get_user_by_username(username):
    """Получение пользователя по имени пользователя"""
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        return dict(user) if user else None
    except Exception as e:
        print(f"Ошибка при получении пользователя: {e}")
        return None
    finally:
        conn.close()

def get_user_by_email(email):
    """Получение пользователя по email"""
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        return dict(user) if user else None
    except Exception as e:
        print(f"Ошибка при получении пользователя: {e}")
        return None
    finally:
        conn.close()

def get_user_by_api_key(api_key):
    """Получение пользователя по API ключу"""
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT * FROM users WHERE api_key = ? AND status = "active"', (api_key,)).fetchone()
        return dict(user) if user else None
    except Exception as e:
        print(f"Ошибка при получении пользователя по API ключу: {e}")
        return None
    finally:
        conn.close()

def get_user_modules(user_id):
    """Получение модулей доступных пользователю"""
    conn = get_db_connection()
    try:
        # Проверяем роль пользователя
        user = get_user_by_id(user_id)
        if not user:
            return []
        
        if user['role'] == 'admin':
            # Администратор получает все модули
            modules = conn.execute('''
                SELECT m.*, 'full' as access_level 
                FROM modules m 
                WHERE m.enabled = 1 
                ORDER BY m.name
            ''').fetchall()
        else:
            # Обычные пользователи получают модули на основе разрешений
            modules = conn.execute('''
                SELECT DISTINCT m.*, COALESCE(uma.access_level, 'view') as access_level
                FROM modules m
                LEFT JOIN module_permissions mp ON m.id = mp.module_id AND mp.role = ?
                LEFT JOIN user_module_access uma ON m.id = uma.module_id AND uma.user_id = ?
                WHERE m.enabled = 1 
                AND (mp.can_view = 1 OR uma.access_level IS NOT NULL OR m.code IN ('medicine', 'energy', 'business'))
                ORDER BY m.name
            ''', (user['role'], user_id)).fetchall()
        
        return [dict(module) for module in modules]
    except Exception as e:
        print(f"Ошибка при получении модулей пользователя: {e}")
        return []
    finally:
        conn.close()

def check_module_access(user_id, module_code, required_access='view'):
    """Проверка доступа пользователя к модулю"""
    conn = get_db_connection()
    try:
        user = get_user_by_id(user_id)
        if not user:
            return False
        
        if user['role'] == 'admin':
            return True
        
        module = conn.execute('SELECT id FROM modules WHERE code = ? AND enabled = 1', (module_code,)).fetchone()
        if not module:
            return False
        
        # Проверяем доступ через таблицу разрешений ролей
        permission = conn.execute('''
            SELECT * FROM module_permissions 
            WHERE module_id = ? AND role = ?
        ''', (module['id'], user['role'])).fetchone()
        
        if permission:
            # Проверяем уровень доступа на основе required_access
            if required_access == 'view' and permission['can_view']:
                return True
            elif required_access == 'edit' and permission['can_edit']:
                return True
            elif required_access == 'delete' and permission['can_delete']:
                return True
            elif required_access == 'manage' and permission['can_manage']:
                return True
        
        # Проверяем доступ через таблицу индивидуального доступа
        user_access = conn.execute('''
            SELECT * FROM user_module_access 
            WHERE user_id = ? AND module_id = ?
        ''', (user_id, module['id'])).fetchone()
        
        if user_access:
            access_levels = ['view', 'edit', 'delete', 'manage']
            user_access_index = access_levels.index(user_access['access_level']) if user_access['access_level'] in access_levels else -1
            required_access_index = access_levels.index(required_access) if required_access in access_levels else -1
            
            if user_access_index >= required_access_index:
                return True
        
        # Для основных модулей разрешаем доступ по умолчанию
        if module_code in ['medicine', 'energy', 'business', 'services', 'clothing', 'transport', 'education', 'construction', 'housing', 'logistics', 'events', 'docs', 'gov', 'security', 'ads', 'courses', 'gifts', 'branding', 'payment']:
            return True
        
        return False
    except Exception as e:
        print(f"Ошибка при проверке доступа к модулю: {e}")
        return False
    finally:
        conn.close()

# ==================== ДЕКОРАТОРЫ ====================

def login_required(f):
    """Декоратор для проверки авторизации"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Для доступа к этой странице необходимо войти в систему', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Декоратор для проверки прав администратора"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Для доступа к этой странице необходимо войти в систему', 'warning')
            return redirect(url_for('login', next=request.url))
        
        user = get_user_by_id(session['user_id'])
        if not user or user['role'] != 'admin':
            flash('У вас недостаточно прав для доступа к этой странице', 'error')
            return redirect(url_for('user_dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function

def module_access_required(module_code, access_level='view'):
    """Декоратор для проверки доступа к модулю"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Для доступа к этой странице необходимо войти в систему', 'warning')
                return redirect(url_for('login', next=request.url))
            
            if not check_module_access(session['user_id'], module_code, access_level):
                flash('У вас нет доступа к этому модулю', 'error')
                return redirect(url_for('user_dashboard'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def api_key_required(f):
    """Декоратор для проверки API ключа"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        
        if not api_key:
            return jsonify({'error': 'API ключ не предоставлен'}), 401
        
        user = get_user_by_api_key(api_key)
        if not user:
            return jsonify({'error': 'Неверный API ключ'}), 401
        
        # Добавляем информацию о пользователе в контекст запроса
        request.user = user
        return f(*args, **kwargs)
    return decorated_function

# ==================== ОСНОВНЫЕ МАРШРУТЫ ====================

@app.route('/')
def index():
    """Главная страница"""
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user and user['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('user_dashboard'))
    
    # Получаем статистику для отображения на главной странице
    conn = get_db_connection()
    stats = {}
    try:
        total_users = conn.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
        total_modules = conn.execute('SELECT COUNT(*) as count FROM modules WHERE enabled = 1').fetchone()['count']
        stats = {
            'total_users': total_users,
            'total_modules': total_modules,
            'site_name': get_system_setting('site_name', 'IDQR Система')
        }
    except Exception as e:
        print(f"Ошибка при получении статистики: {e}")
    finally:
        conn.close()
    
    return render_template('index.html', stats=stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница входа"""
    # Если пользователь уже авторизован, перенаправляем на дашборд
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user and user['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('user_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        
        # Проверяем, включена ли регистрация в системе
        if get_system_setting('registration_enabled', 'true') == 'false':
            flash('Регистрация новых пользователей временно отключена', 'warning')
            return render_template('login.html')
        
        if not username or not password:
            flash('Пожалуйста, заполните все поля', 'error')
            return render_template('login.html')
        
        # Ищем пользователя по имени пользователя или email
        user = get_user_by_username(username)
        if not user:
            user = get_user_by_email(username)
        
        if not user:
            flash('Неверное имя пользователя или пароль', 'error')
            log_activity(None, 'login_failed', 'auth', f'Несуществующий пользователь: {username}')
            return render_template('login.html')
        
        if user['status'] != 'active':
            flash('Ваш аккаунт заблокирован. Обратитесь к администратору.', 'error')
            log_activity(user['id'], 'login_blocked', 'auth', 'Аккаунт заблокирован')
            return render_template('login.html')
        
        if not verify_password(password, user['password_hash']):
            flash('Неверное имя пользователя или пароль', 'error')
            log_activity(user['id'], 'login_failed', 'auth', 'Неверный пароль')
            return render_template('login.html')
        
        # Если требуется подтверждение email
        if get_system_setting('email_verification', 'false') == 'true' and not user['email_verified']:
            flash('Пожалуйста, подтвердите ваш email перед входом', 'warning')
            return redirect(url_for('verify_email_request'))
        
        # Создаем сессию
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        session['theme'] = user['theme']
        session['language'] = user['language']
        
        if remember:
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
        else:
            session.permanent = False
            app.permanent_session_lifetime = timedelta(hours=12)
        
        # Обновляем информацию о пользователе
        conn = get_db_connection()
        try:
            conn.execute('''
                UPDATE users 
                SET last_login = CURRENT_TIMESTAMP, 
                    login_count = login_count + 1,
                    last_activity = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (user['id'],))
            
            # Создаем запись в таблице сессий
            session_id = secrets.token_hex(32)
            expires_at = datetime.now() + app.permanent_session_lifetime
            
            conn.execute('''
                INSERT INTO sessions (user_id, session_id, ip_address, user_agent, expires_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (user['id'], session_id, request.remote_addr, request.user_agent.string, expires_at))
            
            conn.commit()
        except Exception as e:
            print(f"Ошибка при обновлении информации о пользователе: {e}")
        finally:
            conn.close()
        
        # Логируем успешный вход
        log_activity(user['id'], 'login', 'auth', 'Успешный вход в систему')
        
        flash(f'Добро пожаловать, {user["full_name"] or user["username"]}!', 'success')
        
        # Перенаправляем на следующую страницу или дашборд
        next_page = request.args.get('next')
        if next_page and is_safe_url(next_page):
            return redirect(next_page)
        
        if user['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('user_dashboard'))
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Страница регистрации"""
    # Если пользователь уже авторизован, перенаправляем на дашборд
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user and user['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('user_dashboard'))
    
    # Проверяем, включена ли регистрация в системе
    if get_system_setting('registration_enabled', 'true') == 'false':
        flash('Регистрация новых пользователей временно отключена', 'warning')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        full_name = request.form.get('full_name', '').strip()
        phone = request.form.get('phone', '').strip()
        company = request.form.get('company', '').strip()
        position = request.form.get('position', '').strip()
        agree_terms = request.form.get('agree_terms') == 'on'
        
        # Валидация
        errors = []
        
        if not username or len(username) < 3:
            errors.append('Имя пользователя должно содержать минимум 3 символа')
        
        if not email or '@' not in email:
            errors.append('Введите корректный email адрес')
        
        if not password or len(password) < 6:
            errors.append('Пароль должен содержать минимум 6 символов')
        
        if password != confirm_password:
            errors.append('Пароли не совпадают')
        
        if not agree_terms:
            errors.append('Необходимо согласиться с условиями использования')
        
        # Проверяем уникальность имени пользователя и email
        conn = get_db_connection()
        try:
            existing_user = conn.execute('SELECT id FROM users WHERE username = ? OR email = ?', 
                                        (username, email)).fetchone()
            if existing_user:
                errors.append('Пользователь с таким именем или email уже существует')
        except Exception as e:
            print(f"Ошибка при проверке пользователя: {e}")
            errors.append('Произошла ошибка при регистрации')
        finally:
            conn.close()
        
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('register.html', 
                                 username=username, email=email, full_name=full_name,
                                 phone=phone, company=company, position=position)
        
        # Регистрируем пользователя
        conn = get_db_connection()
        try:
            password_hash = hash_password(password)
            verification_token = generate_verification_token() if get_system_setting('email_verification', 'false') == 'true' else None
            
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (username, email, password_hash, full_name, phone, company, position, 
                                 role, status, theme, language, verification_token, email_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (username, email, password_hash, full_name, phone, company, position,
                 'user', 'active', 'light', 'ru', verification_token, verification_token is None))
            
            user_id = cursor.lastrowid
            
            # Генерируем API ключ
            api_key = generate_api_key()
            conn.execute('UPDATE users SET api_key = ? WHERE id = ?', (api_key, user_id))
            
            conn.commit()
            
            # Логируем регистрацию
            log_activity(user_id, 'register', 'auth', 'Регистрация нового пользователя')
            
            # Если требуется подтверждение email
            if verification_token:
                # Здесь должна быть отправка email с подтверждением
                flash('Регистрация прошла успешно! Пожалуйста, проверьте ваш email для подтверждения.', 'success')
                return redirect(url_for('verify_email_request'))
            else:
                flash('Регистрация прошла успешно! Теперь вы можете войти в систему.', 'success')
                return redirect(url_for('login'))
                
        except Exception as e:
            print(f"Ошибка при регистрации пользователя: {e}")
            flash('Произошла ошибка при регистрации. Пожалуйста, попробуйте позже.', 'error')
            return render_template('register.html')
        finally:
            conn.close()
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    """Выход из системы"""
    user_id = session.get('user_id')
    
    # Логируем выход
    log_activity(user_id, 'logout', 'auth', 'Выход из системы')
    
    # Очищаем сессию
    session.clear()
    
    flash('Вы успешно вышли из системы', 'success')
    return redirect(url_for('index'))

# ==================== ДОПОЛНИТЕЛЬНЫЕ МАРШРУТЫ ИЗ СКРИНОВ ====================

@app.route('/about')
def about():
    """Страница 'О компании'"""
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        theme = user['theme'] if user else 'light'
    else:
        theme = get_system_setting('default_theme', 'light')
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_page', 'about', 'Просмотр страницы "О компании"')
    
    return render_template('about.html', theme=theme)

@app.route('/contacts')
def contacts():
    """Страница 'Контакты'"""
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        theme = user['theme'] if user else 'light'
    else:
        theme = get_system_setting('default_theme', 'light')
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_page', 'contacts', 'Просмотр страницы "Контакты"')
    
    return render_template('contacts.html', theme=theme)

@app.route('/portfolio')
def portfolio():
    """Страница 'Портфолио'"""
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        theme = user['theme'] if user else 'light'
    else:
        theme = get_system_setting('default_theme', 'light')
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_page', 'portfolio', 'Просмотр страницы "Портфолио"')
    
    return render_template('portfolio.html', theme=theme)

@app.route('/blog')
def blog():
    """Страница 'Блог'"""
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        theme = user['theme'] if user else 'light'
    else:
        theme = get_system_setting('default_theme', 'light')
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_page', 'blog', 'Просмотр страницы "Блог"')
    
    return render_template('blog.html', theme=theme)

@app.route('/contact-form')
def contact_form():
    """Страница 'Обратная связь'"""
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        theme = user['theme'] if user else 'light'
    else:
        theme = get_system_setting('default_theme', 'light')
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_page', 'contact_form', 'Просмотр страницы "Обратная связь"')
    
    return render_template('contact_form.html', theme=theme)

# ==================== ПОЛЬЗОВАТЕЛЬСКИЕ МАРШРУТЫ ====================

@app.route('/user/dashboard')
@login_required
def user_dashboard():
    """Панель управления пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    # Получаем модули пользователя
    modules = get_user_modules(user['id'])
    
    # Получаем последние активности
    conn = get_db_connection()
    activities = []
    notifications = []
    
    try:
        # Получаем последние активности
        activities = conn.execute('''
            SELECT * FROM user_activity 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT 10
        ''', (user['id'],)).fetchall()
        
        # Получаем непрочитанные уведомления
        notifications = conn.execute('''
            SELECT * FROM notifications 
            WHERE user_id = ? AND is_read = 0 
            ORDER BY created_at DESC 
            LIMIT 5
        ''', (user['id'],)).fetchall()
        
        # Получаем статистику пользователя
        stats = {
            'documents': conn.execute('SELECT COUNT(*) as count FROM documents WHERE user_id = ?', 
                                     (user['id'],)).fetchone()['count'],
            'modules': len(modules),
            'last_login': user['last_login']
        }
        
    except Exception as e:
        print(f"Ошибка при получении данных для дашборда: {e}")
        stats = {'documents': 0, 'modules': 0, 'last_login': None}
    finally:
        conn.close()
    
    # Логируем просмотр дашборда
    log_activity(user['id'], 'view_dashboard', 'user', 'Просмотр панели управления')
    
    return render_template('user_dashboard.html', 
                         user=user, 
                         modules=modules,
                         activities=activities,
                         notifications=notifications,
                         stats=stats)

@app.route('/user/profile', methods=['GET', 'POST'])
@login_required
def user_profile():
    """Профиль пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        phone = request.form.get('phone', '').strip()
        company = request.form.get('company', '').strip()
        position = request.form.get('position', '').strip()
        language = request.form.get('language', 'ru')
        theme = request.form.get('theme', 'light')
        
        # Обновляем аватар
        avatar_file = request.files.get('avatar')
        avatar_path = user.get('avatar')
        
        if avatar_file and avatar_file.filename:
            filename = secure_filename(f"{user['id']}_{avatar_file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'avatars', filename)
            
            # Создаем папку если ее нет
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # Сохраняем файл
            avatar_file.save(filepath)
            avatar_path = f"uploads/avatars/{filename}"
        
        conn = get_db_connection()
        try:
            # Сохраняем старые значения для аудита
            old_values = {
                'full_name': user['full_name'],
                'phone': user['phone'],
                'company': user['company'],
                'position': user['position'],
                'language': user['language'],
                'theme': user['theme'],
                'avatar': user['avatar']
            }
            
            # Обновляем профиль
            conn.execute('''
                UPDATE users 
                SET full_name = ?, phone = ?, company = ?, position = ?, 
                    language = ?, theme = ?, avatar = ?
                WHERE id = ?
            ''', (full_name, phone, company, position, language, theme, avatar_path, user['id']))
            
            # Получаем новые значения
            new_values = {
                'full_name': full_name,
                'phone': phone,
                'company': company,
                'position': position,
                'language': language,
                'theme': theme,
                'avatar': avatar_path
            }
            
            # Аудит изменений
            audit_log(user['id'], 'update_profile', 'user', user['id'], old_values, new_values)
            
            conn.commit()
            
            # Обновляем данные в сессии
            session['theme'] = theme
            session['language'] = language
            
            flash('Профиль успешно обновлен', 'success')
            
            # Обновляем объект пользователя
            user = get_user_by_id(user['id'])
            
        except Exception as e:
            print(f"Ошибка при обновлении профиля: {e}")
            flash('Произошла ошибка при обновлении профиля', 'error')
        finally:
            conn.close()
    
    # Логируем просмотр профиля
    log_activity(user['id'], 'view_profile', 'user', 'Просмотр профиля')
    
    return render_template('user_profile.html', user=user)

@app.route('/user/settings', methods=['GET', 'POST'])
@login_required
def user_settings():
    """Настройки пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'change_password':
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            if not current_password or not new_password or not confirm_password:
                flash('Заполните все поля', 'error')
            elif new_password != confirm_password:
                flash('Новые пароли не совпадают', 'error')
            elif not verify_password(current_password, user['password_hash']):
                flash('Текущий пароль неверен', 'error')
            elif len(new_password) < 6:
                flash('Новый пароль должен содержать минимум 6 символов', 'error')
            else:
                # Меняем пароль
                new_password_hash = hash_password(new_password)
                conn = get_db_connection()
                try:
                    conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', 
                                (new_password_hash, user['id']))
                    conn.commit()
                    
                    # Аудит изменения пароля
                    audit_log(user['id'], 'change_password', 'user', user['id'])
                    
                    # Отправляем уведомление
                    create_notification(user['id'], 'Пароль изменен', 
                                       'Ваш пароль был успешно изменен.', 
                                       'security', 'lock')
                    
                    flash('Пароль успешно изменен', 'success')
                    
                except Exception as e:
                    print(f"Ошибка при изменении пароля: {e}")
                    flash('Произошла ошибка при изменении пароля', 'error')
                finally:
                    conn.close()
        
        elif action == 'update_notifications':
            # Здесь можно добавить логику для обновления настроек уведомлений
            flash('Настройки уведомлений обновлены', 'success')
        
        elif action == 'generate_api_key':
            # Генерируем новый API ключ
            new_api_key = generate_api_key()
            conn = get_db_connection()
            try:
                conn.execute('UPDATE users SET api_key = ? WHERE id = ?', 
                            (new_api_key, user['id']))
                conn.commit()
                
                # Аудит генерации API ключа
                audit_log(user['id'], 'generate_api_key', 'user', user['id'])
                
                flash(f'Новый API ключ сгенерирован: {new_api_key}', 'success')
                
            except Exception as e:
                print(f"Ошибка при генерации API ключа: {e}")
                flash('Произошла ошибка при генерации API ключа', 'error')
            finally:
                conn.close()
    
    # Логируем просмотр настроек
    log_activity(user['id'], 'view_settings', 'user', 'Просмотр настроек')
    
    return render_template('user_settings.html', user=user)

@app.route('/user/notifications')
@login_required
def user_notifications():
    """Уведомления пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    # Получаем все уведомления пользователя
    notifications = get_user_notifications(user['id'], limit=50)
    
    # Логируем просмотр уведомлений
    log_activity(user['id'], 'view_notifications', 'user', 'Просмотр уведомлений')
    
    return render_template('user_notifications.html', user=user, notifications=notifications)

@app.route('/user/notifications/mark-read/<int:notification_id>')
@login_required
def mark_notification_read(notification_id):
    """Пометить уведомление как прочитанное"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        return jsonify({'success': False, 'error': 'Пользователь не найден'}), 401
    
    conn = get_db_connection()
    try:
        # Проверяем, принадлежит ли уведомление пользователю
        notification = conn.execute('SELECT * FROM notifications WHERE id = ? AND user_id = ?', 
                                   (notification_id, user['id'])).fetchone()
        
        if not notification:
            return jsonify({'success': False, 'error': 'Уведомление не найдено'}), 404
        
        # Помечаем как прочитанное
        conn.execute('UPDATE notifications SET is_read = 1, read_at = CURRENT_TIMESTAMP WHERE id = ?', 
                    (notification_id,))
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Ошибка при отметке уведомления как прочитанного: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/user/notifications/mark-all-read')
@login_required
def mark_all_notifications_read():
    """Пометить все уведомления как прочитанные"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        return jsonify({'success': False, 'error': 'Пользователь не найден'}), 401
    
    conn = get_db_connection()
    try:
        conn.execute('''
            UPDATE notifications 
            SET is_read = 1, read_at = CURRENT_TIMESTAMP 
            WHERE user_id = ? AND is_read = 0
        ''', (user['id'],))
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Ошибка при отметке всех уведомлений как прочитанных: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/user/activity')
@login_required
def user_activity():
    """Активность пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    # Получаем параметры фильтрации
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    module_filter = request.args.get('module', '')
    action_filter = request.args.get('action', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # Строим запрос
    conn = get_db_connection()
    try:
        query = 'SELECT * FROM user_activity WHERE user_id = ?'
        params = [user['id']]
        
        if module_filter:
            query += ' AND module = ?'
            params.append(module_filter)
        
        if action_filter:
            query += ' AND action_type = ?'
            params.append(action_filter)
        
        if date_from:
            query += ' AND DATE(created_at) >= ?'
            params.append(date_from)
        
        if date_to:
            query += ' AND DATE(created_at) <= ?'
            params.append(date_to)
        
        query += ' ORDER BY created_at DESC'
        
        # Получаем общее количество записей
        count_query = query.replace('SELECT *', 'SELECT COUNT(*) as count', 1)
        total_count = conn.execute(count_query, params).fetchone()['count']
        
        # Добавляем пагинацию
        query += ' LIMIT ? OFFSET ?'
        params.extend([per_page, (page - 1) * per_page])
        
        activities = conn.execute(query, params).fetchall()
        
        # Получаем уникальные модули для фильтра
        modules = conn.execute('''
            SELECT DISTINCT module FROM user_activity 
            WHERE user_id = ? AND module IS NOT NULL 
            ORDER BY module
        ''', (user['id'],)).fetchall()
        
        # Получаем уникальные действия для фильтра
        actions = conn.execute('''
            SELECT DISTINCT action_type FROM user_activity 
            WHERE user_id = ? AND action_type IS NOT NULL 
            ORDER BY action_type
        ''', (user['id'],)).fetchall()
        
    except Exception as e:
        print(f"Ошибка при получении активности: {e}")
        activities = []
        modules = []
        actions = []
        total_count = 0
    finally:
        conn.close()
    
    # Логируем просмотр активности
    log_activity(user['id'], 'view_activity', 'user', 'Просмотр истории активности')
    
    return render_template('user_activity.html', 
                         user=user, 
                         activities=activities,
                         modules=modules,
                         actions=actions,
                         page=page,
                         per_page=per_page,
                         total_count=total_count,
                         filters={
                             'module': module_filter,
                             'action': action_filter,
                             'date_from': date_from,
                             'date_to': date_to
                         })

# ==================== МОДУЛИ ====================

@app.route('/modules')
def modules_page():
    """Страница всех модулей"""
    # Получаем тему из сессии или настройки системы
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    # Получаем все активные модули
    conn = get_db_connection()
    try:
        modules = conn.execute('''
            SELECT m.*, c.name as category_name, c.icon as category_icon, c.color as category_color
            FROM modules m
            LEFT JOIN module_categories c ON m.category = c.code
            WHERE m.enabled = 1
            ORDER BY c.sort_order, m.name
        ''').fetchall()
        
        # Группируем модули по категориям
        categories = {}
        for module in modules:
            category_name = module['category_name'] or 'Другие'
            if category_name not in categories:
                categories[category_name] = {
                    'icon': module['category_icon'],
                    'color': module['category_color'],
                    'modules': []
                }
            categories[category_name]['modules'].append(dict(module))
        
    except Exception as e:
        print(f"Ошибка при получении модулей: {e}")
        categories = {}
    finally:
        conn.close()
    
    # Если пользователь авторизован, логируем просмотр
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_modules', 'system', 'Просмотр всех модулей')
    
    return render_template('modules.html', categories=categories, theme=theme)

@app.route('/user/modules')
@login_required
def user_modules():
    """Модули пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    # Получаем модули пользователя
    modules = get_user_modules(user['id'])
    
    # Группируем модули по категориям
    categories = {}
    for module in modules:
        category = module.get('category', 'other')
        if category not in categories:
            categories[category] = []
        categories[category].append(module)
    
    # Логируем просмотр модулей
    log_activity(user['id'], 'view_user_modules', 'user', 'Просмотр доступных модулей')
    
    return render_template('user_modules.html', user=user, categories=categories)

# ==================== ВСЕ МОДУЛИ (ОБЩИЕ СТРАНИЦЫ) ====================

@app.route('/dashboard/services')
@app.route('/services')
def services_page():
    """Страница услуг и быта"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('services',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле услуг: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'services', 'Просмотр страницы услуг и быта')
    
    return render_template('services.html', theme=theme, module=module)

@app.route('/modules/clothing')
@app.route('/clothing')
def clothing_page():
    """Страница одежды и моды"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('clothing',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле одежды: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'clothing', 'Просмотр страницы одежды и моды')
    
    return render_template('clothing.html', theme=theme, module=module)

@app.route('/modules/transport')
@app.route('/transport')
def transport_page():
    """Страница транспорта и авто"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('transport',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле транспорта: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'transport', 'Просмотр страницы транспорта и авто')
    
    return render_template('transport.html', theme=theme, module=module)

@app.route('/modules/education')
@app.route('/education')
def education_page():
    """Страница образования и школ"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('education',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле образования: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'education', 'Просмотр страницы образования и школ')
    
    return render_template('education.html', theme=theme, module=module)

@app.route('/dashboard/medicine')
@app.route('/medicine')
def medicine_page():
    """Общая страница медицины"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('medicine',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле медицины: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_medicine', 'medicine', 'Просмотр страницы медицины')
    
    return render_template('medicine.html', theme=theme, module=module)

@app.route('/modules/construction')
@app.route('/construction')
def construction_page():
    """Страница стройки и объектов"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('construction',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле строительства: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'construction', 'Просмотр страницы стройки и объектов')
    
    return render_template('construction.html', theme=theme, module=module)

@app.route('/dashboard/business')
@app.route('/business')
def business_page():
    """Общая страница бизнеса"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('business',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле бизнеса: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_business', 'business', 'Просмотр страницы бизнеса')
    
    return render_template('business.html', theme=theme, module=module)

@app.route('/modules/logistics')
@app.route('/logistics')
def logistics_page():
    """Страница склада и логистики"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('logistics',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле логистики: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'logistics', 'Просмотр страницы склада и логистики')
    
    return render_template('logistics.html', theme=theme, module=module)

@app.route('/modules/housing')
@app.route('/housing')
def housing_page():
    """Страница ЖКХ и дома"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('housing',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле ЖКХ: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'housing', 'Просмотр страницы ЖКХ и дома')
    
    return render_template('housing.html', theme=theme, module=module)

@app.route('/modules/events')
@app.route('/events')
def events_page():
    """Страница событий и входа"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('events',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле событий: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'events', 'Просмотр страницы событий и входа')
    
    return render_template('events.html', theme=theme, module=module)

@app.route('/modules/docs')
@app.route('/docs')
def docs_page():
    """Страница документов и удостоверения"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('docs',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле документов: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'docs', 'Просмотр страницы документов и удостоверений')
    
    return render_template('docs.html', theme=theme, module=module)

@app.route('/modules/gov')
@app.route('/gov')
def gov_page():
    """Страница госуслуг и учета"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('gov',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле госуслуг: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'gov', 'Просмотр страницы госуслуг и учета')
    
    return render_template('gov.html', theme=theme, module=module)

@app.route('/modules/security')
@app.route('/security')
def security_page():
    """Страница безопасности и контроля"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('security',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле безопасности: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'security', 'Просмотр страницы безопасности и контроля')
    
    return render_template('security.html', theme=theme, module=module)

@app.route('/modules/ads')
@app.route('/ads')
def ads_page():
    """Страница рекламы и аналитики"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('ads',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле рекламы: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'ads', 'Просмотр страницы рекламы и аналитики')
    
    return render_template('ads.html', theme=theme, module=module)

@app.route('/modules/courses')
@app.route('/courses')
def courses_page():
    """Страница курсов и тренингов"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('courses',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле курсов: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'courses', 'Просмотр страницы курсов и тренингов')
    
    return render_template('courses.html', theme=theme, module=module)

@app.route('/modules/gifts')
@app.route('/gifts')
def gifts_page():
    """Страница подарков и сервиса"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('gifts',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле подарков: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'gifts', 'Просмотр страницы подарков и сервиса')
    
    return render_template('gifts.html', theme=theme, module=module)

@app.route('/modules/branding')
@app.route('/branding')
def branding_page():
    """Страница маркетинга и брендов"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('branding',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле маркетинга: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'branding', 'Просмотр страницы маркетинга и брендов')
    
    return render_template('branding.html', theme=theme, module=module)

@app.route('/modules/payment')
@app.route('/payment')
def payment_page():
    """Страница квитанций и оплаты"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('payment',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле оплаты: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_module', 'payment', 'Просмотр страницы квитанций и оплаты')
    
    return render_template('payment.html', theme=theme, module=module)

@app.route('/dashboard/energy')
@app.route('/energy')
def energy_page():
    """Общая страница энергетики"""
    theme = 'light'
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            theme = user['theme']
    else:
        theme = get_system_setting('default_theme', 'light')
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    if 'user_id' in session:
        log_activity(session['user_id'], 'view_energy', 'energy', 'Просмотр страницы энергетики')
    
    return render_template('energy.html', theme=theme, module=module)

# ==================== ПОЛЬЗОВАТЕЛЬСКИЕ МОДУЛИ ====================

@app.route('/user/services')
@login_required
@module_access_required('services', 'view')
def user_services():
    """Модуль услуг для пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('services',)).fetchone()
        module = dict(module) if module else None
        
        stats = {
            'total_activities': conn.execute('SELECT COUNT(*) as count FROM user_activity WHERE user_id = ? AND module = ?', 
                                           (user['id'], 'services')).fetchone()['count'],
            'last_access': conn.execute('SELECT MAX(created_at) as last_access FROM user_activity WHERE user_id = ? AND module = ?', 
                                      (user['id'], 'services')).fetchone()['last_access']
        }
    except Exception as e:
        print(f"Ошибка при получении информации о модуле услуг: {e}")
        module = None
        stats = {}
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'services', 'Доступ к модулю услуг и быта')
    
    return render_template('user_services.html', user=user, module=module, stats=stats)

@app.route('/user/cleaning')
@login_required
@module_access_required('services', 'view')
def user_cleaning():
    """Модуль уборки и гигиены"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('services',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле услуг: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'services', 'Доступ к модулю уборки и гигиены')
    
    return render_template('user_cleaning.html', user=user, module=module)

@app.route('/user/business')
@login_required
@module_access_required('business', 'view')
def user_business():
    """Модуль бизнеса для пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('business',)).fetchone()
        module = dict(module) if module else None
        
        stats = {
            'total_activities': conn.execute('SELECT COUNT(*) as count FROM user_activity WHERE user_id = ? AND module = ?', 
                                           (user['id'], 'business')).fetchone()['count'],
            'last_access': conn.execute('SELECT MAX(created_at) as last_access FROM user_activity WHERE user_id = ? AND module = ?', 
                                      (user['id'], 'business')).fetchone()['last_access']
        }
    except Exception as e:
        print(f"Ошибка при получении информации о модуле бизнеса: {e}")
        module = None
        stats = {}
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'business', 'Доступ к модулю бизнеса и магазинов')
    
    return render_template('user_business.html', user=user, module=module, stats=stats)

@app.route('/user/medicine')
@login_required
@module_access_required('medicine', 'view')
def user_medicine():
    """Модуль медицины для пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('medicine',)).fetchone()
        module = dict(module) if module else None
        
        stats = {
            'total_activities': conn.execute('SELECT COUNT(*) as count FROM user_activity WHERE user_id = ? AND module = ?', 
                                           (user['id'], 'medicine')).fetchone()['count'],
            'last_access': conn.execute('SELECT MAX(created_at) as last_access FROM user_activity WHERE user_id = ? AND module = ?', 
                                      (user['id'], 'medicine')).fetchone()['last_access']
        }
    except Exception as e:
        print(f"Ошибка при получении информации о модуле медицины: {e}")
        module = None
        stats = {}
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'medicine', 'Доступ к модулю медицины')
    
    return render_template('user_medicine.html', user=user, module=module, stats=stats)

@app.route('/user/energy')
@login_required
@module_access_required('energy', 'view')
def user_energy():
    """Модуль энергетики для пользователя"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
        
        stats = {
            'total_activities': conn.execute('SELECT COUNT(*) as count FROM user_activity WHERE user_id = ? AND module = ?', 
                                           (user['id'], 'energy')).fetchone()['count'],
            'last_access': conn.execute('SELECT MAX(created_at) as last_access FROM user_activity WHERE user_id = ? AND module = ?', 
                                      (user['id'], 'energy')).fetchone()['last_access']
        }
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
        stats = {}
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к модулю энергетики')
    
    return render_template('user_energy.html', user=user, module=module, stats=stats)

# ==================== ЭНЕРГЕТИКА - ДОПОЛНИТЕЛЬНЫЕ СТРАНИЦЫ ====================

@app.route('/energy/complaints')
@login_required
@module_access_required('energy', 'view')
def energy_complaints():
    """Жалобы и обращения в энергетике"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к жалобам и обращениям в энергетике')
    
    return render_template('energy_complaints.html', user=user, module=module)

@app.route('/energy/analytics')
@login_required
@module_access_required('energy', 'view')
def energy_analytics():
    """Аналитика энергетики"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к аналитике энергетики')
    
    return render_template('energy_analytics.html', user=user, module=module)

@app.route('/energy/documents')
@login_required
@module_access_required('energy', 'view')
def energy_documents():
    """Документы энергетики"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к документам энергетики')
    
    return render_template('energy_documents.html', user=user, module=module)

@app.route('/energy/electricity')
@login_required
@module_access_required('energy', 'view')
def energy_electricity():
    """Электричество"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к электричеству')
    
    return render_template('energy_electricity.html', user=user, module=module)

@app.route('/energy/heat_gas')
@login_required
@module_access_required('energy', 'view')
def energy_heat_gas():
    """Тепло и газ"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к теплу и газу')
    
    return render_template('energy_heat_gas.html', user=user, module=module)

@app.route('/energy/inspections')
@login_required
@module_access_required('energy', 'view')
def energy_inspections():
    """Инспекции"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к инспекциям')
    
    return render_template('energy_inspections.html', user=user, module=module)

@app.route('/energy/meters')
@login_required
@module_access_required('energy', 'view')
def energy_meters():
    """Счетчики"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к счетчикам')
    
    return render_template('energy_meters.html', user=user, module=module)

@app.route('/energy/renewable')
@login_required
@module_access_required('energy', 'view')
def energy_renewable():
    """Возобновляемая энергия"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к возобновляемой энергии')
    
    return render_template('energy_renewable.html', user=user, module=module)

@app.route('/energy/suppliers')
@login_required
@module_access_required('energy', 'view')
def energy_suppliers():
    """Поставщики"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        module = conn.execute('SELECT * FROM modules WHERE code = ?', ('energy',)).fetchone()
        module = dict(module) if module else None
    except Exception as e:
        print(f"Ошибка при получении информации о модуле энергетики: {e}")
        module = None
    finally:
        conn.close()
    
    log_activity(user['id'], 'access_module', 'energy', 'Доступ к поставщикам')
    
    return render_template('energy_suppliers.html', user=user, module=module)

# ==================== КОНТАКТЫ И НАСТРОЙКИ ====================

@app.route('/user/contact')
@login_required
def user_contact():
    """Страница контактов"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    log_activity(user['id'], 'view_contact', 'user', 'Просмотр страницы контактов')
    
    return render_template('user_contact.html', user=user)

# ==================== ДОПОЛНИТЕЛЬНЫЕ МАРШРУТЫ ИЗ СКРИНОВ ====================

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Панель администратора"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    # Получаем статистику для админ-панели
    conn = get_db_connection()
    stats = {}
    
    try:
        # Общая статистика
        stats['total_users'] = conn.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
        stats['active_users'] = conn.execute('SELECT COUNT(*) as count FROM users WHERE status = "active"').fetchone()['count']
        stats['total_modules'] = conn.execute('SELECT COUNT(*) as count FROM modules').fetchone()['count']
        stats['active_modules'] = conn.execute('SELECT COUNT(*) as count FROM modules WHERE enabled = 1').fetchone()['count']
        
        # Статистика за сегодня
        today = datetime.now().strftime('%Y-%m-%d')
        stats['new_users_today'] = conn.execute('SELECT COUNT(*) as count FROM users WHERE DATE(created_at) = ?', 
                                               (today,)).fetchone()['count']
        stats['logins_today'] = conn.execute('SELECT COUNT(*) as count FROM user_activity WHERE action_type = "login" AND DATE(created_at) = ?', 
                                           (today,)).fetchone()['count']
        
        # Последние активности
        recent_activities = conn.execute('''
            SELECT ua.*, u.username, u.full_name 
            FROM user_activity ua
            LEFT JOIN users u ON ua.user_id = u.id
            ORDER BY ua.created_at DESC 
            LIMIT 10
        ''').fetchall()
        
        # Последние пользователи
        recent_users = conn.execute('''
            SELECT * FROM users 
            ORDER BY created_at DESC 
            LIMIT 10
        ''').fetchall()
        
        # Статистика по модулям
        module_stats = conn.execute('''
            SELECT m.name, COUNT(ua.id) as activity_count
            FROM modules m
            LEFT JOIN user_activity ua ON m.code = ua.module
            GROUP BY m.id, m.name
            ORDER BY activity_count DESC
            LIMIT 10
        ''').fetchall()
        
    except Exception as e:
        print(f"Ошибка при получении статистики для админ-панели: {e}")
        recent_activities = []
        recent_users = []
        module_stats = []
    finally:
        conn.close()
    
    # Логируем доступ к админ-панели
    log_activity(user['id'], 'access_admin', 'admin', 'Доступ к панели администратора')
    
    return render_template('admin_dashboard.html', 
                         user=user, 
                         stats=stats,
                         recent_activities=recent_activities,
                         recent_users=recent_users,
                         module_stats=module_stats)

@app.route('/admin/users')
@admin_required
def admin_users():
    """Управление пользователями"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    # Получаем параметры фильтрации и сортировки
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', '')
    role = request.args.get('role', '')
    status = request.args.get('status', '')
    sort_by = request.args.get('sort_by', 'created_at')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Строим запрос
    conn = get_db_connection()
    try:
        query = 'SELECT * FROM users WHERE 1=1'
        params = []
        
        if search:
            query += ' AND (username LIKE ? OR email LIKE ? OR full_name LIKE ? OR phone LIKE ?)'
            search_term = f'%{search}%'
            params.extend([search_term, search_term, search_term, search_term])
        
        if role:
            query += ' AND role = ?'
            params.append(role)
        
        if status:
            query += ' AND status = ?'
            params.append(status)
        
        # Добавляем сортировку
        if sort_by in ['username', 'email', 'full_name', 'role', 'status', 'created_at', 'last_login']:
            query += f' ORDER BY {sort_by} {sort_order}'
        else:
            query += ' ORDER BY created_at DESC'
        
        # Получаем общее количество записей
        count_query = query.replace('SELECT *', 'SELECT COUNT(*) as count', 1)
        total_count = conn.execute(count_query, params).fetchone()['count']
        
        # Добавляем пагинацию
        query += ' LIMIT ? OFFSET ?'
        params.extend([per_page, (page - 1) * per_page])
        
        users = conn.execute(query, params).fetchall()
        
        # Получаем статистику по ролям
        role_stats = conn.execute('''
            SELECT role, COUNT(*) as count 
            FROM users 
            GROUP BY role 
            ORDER BY count DESC
        ''').fetchall()
        
        # Получаем статистику по статусам
        status_stats = conn.execute('''
            SELECT status, COUNT(*) as count 
            FROM users 
            GROUP BY status 
            ORDER BY count DESC
        ''').fetchall()
        
    except Exception as e:
        print(f"Ошибка при получении пользователей: {e}")
        users = []
        role_stats = []
        status_stats = []
        total_count = 0
    finally:
        conn.close()
    
    # Логируем просмотр пользователей
    log_activity(user['id'], 'view_users', 'admin', 'Просмотр списка пользователей')
    
    return render_template('admin_users.html', 
                         user=user, 
                         users=users,
                         role_stats=role_stats,
                         status_stats=status_stats,
                         page=page,
                         per_page=per_page,
                         total_count=total_count,
                         filters={
                             'search': search,
                             'role': role,
                             'status': status,
                             'sort_by': sort_by,
                             'sort_order': sort_order
                         })

@app.route('/admin/user/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def admin_user_detail(user_id):
    """Детальная информация о пользователе"""
    admin_user = get_user_by_id(session['user_id'])
    
    if not admin_user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_user':
            # Получаем данные из формы
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            full_name = request.form.get('full_name', '').strip()
            phone = request.form.get('phone', '').strip()
            company = request.form.get('company', '').strip()
            position = request.form.get('position', '').strip()
            role = request.form.get('role', 'user')
            status = request.form.get('status', 'active')
            theme = request.form.get('theme', 'light')
            language = request.form.get('language', 'ru')
            
            try:
                # Получаем старые значения для аудита
                old_user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
                
                # Обновляем пользователя
                conn.execute('''
                    UPDATE users 
                    SET username = ?, email = ?, full_name = ?, phone = ?, company = ?, 
                        position = ?, role = ?, status = ?, theme = ?, language = ?
                    WHERE id = ?
                ''', (username, email, full_name, phone, company, position, role, status, theme, language, user_id))
                
                # Аудит изменений
                new_values = {
                    'username': username,
                    'email': email,
                    'full_name': full_name,
                    'phone': phone,
                    'company': company,
                    'position': position,
                    'role': role,
                    'status': status,
                    'theme': theme,
                    'language': language
                }
                
                audit_log(admin_user['id'], 'update_user', 'user', user_id, dict(old_user), new_values)
                
                conn.commit()
                flash('Пользователь успешно обновлен', 'success')
                
            except Exception as e:
                print(f"Ошибка при обновлении пользователя: {e}")
                flash('Произошла ошибка при обновлении пользователя', 'error')
        
        elif action == 'delete_user':
            try:
                # Нельзя удалить себя
                if user_id == admin_user['id']:
                    flash('Вы не можете удалить свой собственный аккаунт', 'error')
                else:
                    # Удаляем пользователя
                    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
                    conn.commit()
                    
                    # Аудит удаления
                    audit_log(admin_user['id'], 'delete_user', 'user', user_id)
                    
                    flash('Пользователь успешно удален', 'success')
                    return redirect(url_for('admin_users'))
                    
            except Exception as e:
                print(f"Ошибка при удалении пользователя: {e}")
                flash('Произошла ошибка при удалении пользователя', 'error')
        
        elif action == 'reset_password':
            new_password = secrets.token_hex(8)  # Генерируем случайный пароль
            password_hash = hash_password(new_password)
            
            try:
                conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', 
                            (password_hash, user_id))
                conn.commit()
                
                # Аудит сброса пароля
                audit_log(admin_user['id'], 'reset_password', 'user', user_id)
                
                flash(f'Пароль сброшен. Новый пароль: {new_password}', 'success')
                
            except Exception as e:
                print(f"Ошибка при сбросе пароля: {e}")
                flash('Произошла ошибка при сбросе пароля', 'error')
    
    # Получаем информацию о пользователе
    try:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        
        if not user:
            flash('Пользователь не найден', 'error')
            return redirect(url_for('admin_users'))
        
        # Получаем активность пользователя
        activities = conn.execute('''
            SELECT * FROM user_activity 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT 20
        ''', (user_id,)).fetchall()
        
        # Получаем модули пользователя
        user_modules = get_user_modules(user_id)
        
        # Получаем статистику пользователя
        stats = {
            'total_activities': conn.execute('SELECT COUNT(*) as count FROM user_activity WHERE user_id = ?', 
                                           (user_id,)).fetchone()['count'],
            'total_logins': conn.execute('SELECT COUNT(*) as count FROM user_activity WHERE user_id = ? AND action_type = "login"', 
                                       (user_id,)).fetchone()['count'],
            'documents_count': conn.execute('SELECT COUNT(*) as count FROM documents WHERE user_id = ?', 
                                          (user_id,)).fetchone()['count']
        }
        
    except Exception as e:
        print(f"Ошибка при получении информации о пользователе: {e}")
        flash('Произошла ошибка при получении информации о пользователе', 'error')
        return redirect(url_for('admin_users'))
    finally:
        conn.close()
    
    # Логируем просмотр пользователя
    log_activity(admin_user['id'], 'view_user_detail', 'admin', f'Просмотр пользователя {user["username"]}')
    
    return render_template('admin_user_detail.html', 
                         admin_user=admin_user, 
                         user=dict(user),
                         activities=activities,
                         user_modules=user_modules,
                         stats=stats)

@app.route('/admin/modules')
@admin_required
def admin_modules():
    """Управление модулями"""
    user = get_user_by_id(session['user_id'])
    
    if not user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    # Получаем все модули
    conn = get_db_connection()
    try:
        modules = conn.execute('''
            SELECT m.*, COUNT(DISTINCT uma.user_id) as user_count
            FROM modules m
            LEFT JOIN user_module_access uma ON m.id = uma.module_id
            GROUP BY m.id
            ORDER BY m.name
        ''').fetchall()
        
        # Получаем категории модулей
        categories = conn.execute('SELECT * FROM module_categories WHERE is_active = 1 ORDER BY sort_order').fetchall()
        
    except Exception as e:
        print(f"Ошибка при получении модулей: {e}")
        modules = []
        categories = []
    finally:
        conn.close()
    
    # Логируем просмотр модулей
    log_activity(user['id'], 'view_modules_admin', 'admin', 'Просмотр управления модулями')
    
    return render_template('admin_modules.html', user=user, modules=modules, categories=categories)

@app.route('/admin/module/<int:module_id>', methods=['GET', 'POST'])
@admin_required
def admin_module_detail(module_id):
    """Редактирование модуля"""
    admin_user = get_user_by_id(session['user_id'])
    
    if not admin_user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_module':
            # Получаем данные из формы
            name = request.form.get('name', '').strip()
            code = request.form.get('code', '').strip()
            description = request.form.get('description', '').strip()
            icon = request.form.get('icon', '').strip()
            category = request.form.get('category', '').strip()
            version = request.form.get('version', '1.0.0').strip()
            author = request.form.get('author', '').strip()
            enabled = request.form.get('enabled') == 'on'
            
            try:
                # Получаем старые значения для аудита
                old_module = conn.execute('SELECT * FROM modules WHERE id = ?', (module_id,)).fetchone()
                
                # Обновляем модуль
                conn.execute('''
                    UPDATE modules 
                    SET name = ?, code = ?, description = ?, icon = ?, category = ?, 
                        version = ?, author = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (name, code, description, icon, category, version, author, enabled, module_id))
                
                # Аудит изменений
                new_values = {
                    'name': name,
                    'code': code,
                    'description': description,
                    'icon': icon,
                    'category': category,
                    'version': version,
                    'author': author,
                    'enabled': enabled
                }
                
                audit_log(admin_user['id'], 'update_module', 'module', module_id, dict(old_module), new_values)
                
                conn.commit()
                flash('Модуль успешно обновлен', 'success')
                
            except Exception as e:
                print(f"Ошибка при обновлении модуля: {e}")
                flash('Произошла ошибка при обновлении модуля', 'error')
        
        elif action == 'delete_module':
            try:
                # Удаляем модуль
                conn.execute('DELETE FROM modules WHERE id = ?', (module_id,))
                conn.commit()
                
                # Аудит удаления
                audit_log(admin_user['id'], 'delete_module', 'module', module_id)
                
                flash('Модуль успешно удален', 'success')
                return redirect(url_for('admin_modules'))
                
            except Exception as e:
                print(f"Ошибка при удалении модуля: {e}")
                flash('Произошла ошибка при удалении модуля', 'error')
    
    # Получаем информацию о модуле
    try:
        module = conn.execute('SELECT * FROM modules WHERE id = ?', (module_id,)).fetchone()
        
        if not module:
            flash('Модуль не найден', 'error')
            return redirect(url_for('admin_modules'))
        
        # Получаем разрешения модуля
        permissions = conn.execute('SELECT * FROM module_permissions WHERE module_id = ?', (module_id,)).fetchall()
        
        # Получаем пользователей с доступом к модулю
        users_with_access = conn.execute('''
            SELECT u.*, uma.access_level, uma.granted_at
            FROM users u
            JOIN user_module_access uma ON u.id = uma.user_id
            WHERE uma.module_id = ?
            ORDER BY u.username
        ''', (module_id,)).fetchall()
        
        # Получаем все пользователи для выпадающего списка
        all_users = conn.execute('SELECT id, username, full_name FROM users ORDER BY username').fetchall()
        
    except Exception as e:
        print(f"Ошибка при получении информации о модуле: {e}")
        flash('Произошла ошибка при получении информации о модуле', 'error')
        return redirect(url_for('admin_modules'))
    finally:
        conn.close()
    
    # Логируем просмотр модуля
    log_activity(admin_user['id'], 'view_module_detail', 'admin', f'Просмотр модуля {module["name"]}')
    
    return render_template('admin_module_detail.html', 
                         admin_user=admin_user, 
                         module=dict(module),
                         permissions=permissions,
                         users_with_access=users_with_access,
                         all_users=all_users)

@app.route('/admin/activity')
@admin_required
def admin_activity():
    """Просмотр активности всех пользователей"""
    admin_user = get_user_by_id(session['user_id'])
    
    if not admin_user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    # Получаем параметры фильтрации
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    user_id = request.args.get('user_id', type=int)
    module = request.args.get('module', '')
    action_type = request.args.get('action_type', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # Строим запрос
    conn = get_db_connection()
    try:
        query = '''
            SELECT ua.*, u.username, u.full_name 
            FROM user_activity ua
            LEFT JOIN users u ON ua.user_id = u.id
            WHERE 1=1
        '''
        params = []
        
        if user_id:
            query += ' AND ua.user_id = ?'
            params.append(user_id)
        
        if module:
            query += ' AND ua.module = ?'
            params.append(module)
        
        if action_type:
            query += ' AND ua.action_type = ?'
            params.append(action_type)
        
        if date_from:
            query += ' AND DATE(ua.created_at) >= ?'
            params.append(date_from)
        
        if date_to:
            query += ' AND DATE(ua.created_at) <= ?'
            params.append(date_to)
        
        query += ' ORDER BY ua.created_at DESC'
        
        # Получаем общее количество записей
        count_query = query.replace('SELECT ua.*, u.username, u.full_name', 'SELECT COUNT(*) as count', 1)
        total_count = conn.execute(count_query, params).fetchone()['count']
        
        # Добавляем пагинацию
        query += ' LIMIT ? OFFSET ?'
        params.extend([per_page, (page - 1) * per_page])
        
        activities = conn.execute(query, params).fetchall()
        
        # Получаем уникальные модули для фильтра
        modules = conn.execute('SELECT DISTINCT module FROM user_activity WHERE module IS NOT NULL ORDER BY module').fetchall()
        
        # Получаем уникальные действия для фильтра
        action_types = conn.execute('SELECT DISTINCT action_type FROM user_activity WHERE action_type IS NOT NULL ORDER BY action_type').fetchall()
        
        # Получаем пользователей для фильтра
        users = conn.execute('SELECT id, username, full_name FROM users ORDER BY username').fetchall()
        
    except Exception as e:
        print(f"Ошибка при получении активности: {e}")
        activities = []
        modules = []
        action_types = []
        users = []
        total_count = 0
    finally:
        conn.close()
    
    # Логируем просмотр активности
    log_activity(admin_user['id'], 'view_activity_admin', 'admin', 'Просмотр активности системы')
    
    return render_template('admin_activity.html', 
                         admin_user=admin_user, 
                         activities=activities,
                         modules=modules,
                         action_types=action_types,
                         users=users,
                         page=page,
                         per_page=per_page,
                         total_count=total_count,
                         filters={
                             'user_id': user_id,
                             'module': module,
                             'action_type': action_type,
                             'date_from': date_from,
                             'date_to': date_to
                         })

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    """Настройки системы"""
    admin_user = get_user_by_id(session['user_id'])
    
    if not admin_user:
        session.clear()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_settings':
            # Обновляем настройки из формы
            for key in request.form:
                if key.startswith('setting_'):
                    setting_key = key.replace('setting_', '')
                    setting_value = request.form.get(key)
                    update_system_setting(setting_key, setting_value)
            
            # Аудит изменений настроек
            audit_log(admin_user['id'], 'update_settings', 'system', None, None, {'settings_updated': True})
            
            flash('Настройки успешно обновлены', 'success')
        
        elif action == 'add_category':
            # Добавляем новую категорию модулей
            name = request.form.get('category_name', '').strip()
            code = request.form.get('category_code', '').strip()
            description = request.form.get('category_description', '').strip()
            icon = request.form.get('category_icon', '').strip()
            color = request.form.get('category_color', '#6C63FF').strip()
            sort_order = request.form.get('category_sort_order', 0, type=int)
            
            if name and code:
                conn = get_db_connection()
                try:
                    conn.execute('''
                        INSERT INTO module_categories (name, code, description, icon, color, sort_order)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (name, code, description, icon, color, sort_order))
                    conn.commit()
                    
                    # Аудит добавления категории
                    audit_log(admin_user['id'], 'add_category', 'system', None, None, {
                        'name': name,
                        'code': code,
                        'description': description
                    })
                    
                    flash('Категория успешно добавлена', 'success')
                except Exception as e:
                    print(f"Ошибка при добавлении категории: {e}")
                    flash('Произошла ошибка при добавлении категории', 'error')
                finally:
                    conn.close()
            else:
                flash('Заполните название и код категории', 'error')
    
    # Получаем все настройки системы
    conn = get_db_connection()
    try:
        settings = conn.execute('SELECT * FROM system_settings ORDER BY category, setting_key').fetchall()
        
        # Группируем настройки по категориям
        settings_by_category = {}
        for setting in settings:
            category = setting['category'] or 'other'
            if category not in settings_by_category:
                settings_by_category[category] = []
            settings_by_category[category].append(dict(setting))
        
        # Получаем категории модулей
        categories = conn.execute('SELECT * FROM module_categories ORDER BY sort_order').fetchall()
        
    except Exception as e:
        print(f"Ошибка при получении настроек: {e}")
        settings_by_category = {}
        categories = []
    finally:
        conn.close()
    
    # Логируем просмотр настроек
    log_activity(admin_user['id'], 'view_settings_admin', 'admin', 'Просмотр настроек системы')
    
    return render_template('admin_settings.html', 
                         admin_user=admin_user, 
                         settings_by_category=settings_by_category,
                         categories=categories)

# ==================== API МАРШРУТЫ ====================

@app.route('/api/v1/user/profile', methods=['GET'])
@api_key_required
def api_user_profile():
    """API для получения профиля пользователя"""
    user = request.user
    
    return jsonify({
        'success': True,
        'data': {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'full_name': user['full_name'],
            'phone': user['phone'],
            'company': user['company'],
            'position': user['position'],
            'role': user['role'],
            'status': user['status'],
            'theme': user['theme'],
            'language': user['language'],
            'created_at': user['created_at'],
            'last_login': user['last_login']
        }
    })

@app.route('/api/v1/user/modules', methods=['GET'])
@api_key_required
def api_user_modules():
    """API для получения модулей пользователя"""
    user = request.user
    modules = get_user_modules(user['id'])
    
    # Форматируем модули для API
    formatted_modules = []
    for module in modules:
        formatted_modules.append({
            'id': module['id'],
            'name': module['name'],
            'code': module['code'],
            'description': module['description'],
            'icon': module['icon'],
            'category': module['category'],
            'version': module['version'],
            'access_level': module.get('access_level', 'view')
        })
    
    return jsonify({
        'success': True,
        'data': formatted_modules
    })

@app.route('/api/v1/system/stats', methods=['GET'])
@api_key_required
def api_system_stats():
    """API для получения статистики системы"""
    conn = get_db_connection()
    
    try:
        # Получаем статистику
        stats = {
            'total_users': conn.execute('SELECT COUNT(*) as count FROM users').fetchone()['count'],
            'active_users': conn.execute('SELECT COUNT(*) as count FROM users WHERE status = "active"').fetchone()['count'],
            'total_modules': conn.execute('SELECT COUNT(*) as count FROM modules').fetchone()['count'],
            'active_modules': conn.execute('SELECT COUNT(*) as count FROM modules WHERE enabled = 1').fetchone()['count'],
            'total_activities': conn.execute('SELECT COUNT(*) as count FROM user_activity').fetchone()['count'],
            'storage_used': conn.execute('SELECT COALESCE(SUM(file_size), 0) as total FROM documents').fetchone()['total']
        }
        
        return jsonify({
            'success': True,
            'data': stats
        })
        
    except Exception as e:
        print(f"Ошибка при получении статистики: {e}")
        return jsonify({
            'success': False,
            'error': 'Ошибка при получении статистики'
        }), 500
    finally:
        conn.close()

@app.route('/api/v1/auth/login', methods=['POST'])
def api_auth_login():
    """API для аутентификации"""
    data = request.get_json()
    
    if not data:
        return jsonify({'success': False, 'error': 'Неверный формат данных'}), 400
    
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Не указаны имя пользователя или пароль'}), 400
    
    # Ищем пользователя
    user = get_user_by_username(username)
    if not user:
        user = get_user_by_email(username)
    
    if not user:
        return jsonify({'success': False, 'error': 'Неверное имя пользователя или пароль'}), 401
    
    if user['status'] != 'active':
        return jsonify({'success': False, 'error': 'Аккаунт заблокирован'}), 403
    
    if not verify_password(password, user['password_hash']):
        return jsonify({'success': False, 'error': 'Неверное имя пользователя или пароль'}), 401
    
    # Логируем успешный вход через API
    log_activity(user['id'], 'api_login', 'auth', 'Успешный вход через API')
    
    return jsonify({
        'success': True,
        'data': {
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email'],
                'full_name': user['full_name'],
                'role': user['role']
            },
            'api_key': user['api_key'],
            'token': secrets.token_hex(32)  # Временный токен для сессии API
        }
    })

# ==================== УТИЛИТЫ ====================

@app.context_processor
def inject_user():
    """Добавляем пользователя во все шаблоны"""
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user:
            return {'current_user': user}
    return {'current_user': None}

@app.context_processor
def inject_settings():
    """Добавляем настройки системы во все шаблоны"""
    return {
        'site_name': get_system_setting('site_name', 'IDQR Система'),
        'site_description': get_system_setting('site_description', 'Комплексная система управления'),
        'registration_enabled': get_system_setting('registration_enabled', 'true') == 'true',
        'default_theme': get_system_setting('default_theme', 'light')
    }

@app.before_request
def before_request():
    """Обработка перед каждым запросом"""
    # Проверяем режим обслуживания
    if get_system_setting('maintenance_mode', 'false') == 'true':
        # Разрешаем доступ только администраторам
        if request.endpoint and request.endpoint not in ['login', 'static']:
            if 'user_id' not in session:
                return "Сайт на техническом обслуживании. Пожалуйста, зайдите позже.", 503
            
            user = get_user_by_id(session['user_id'])
            if not user or user['role'] != 'admin':
                return "Сайт на техническом обслуживании. Пожалуйста, зайдите позже.", 503
    
    # Обновляем время последней активности для авторизованных пользователей
    if 'user_id' in session:
        conn = get_db_connection()
        try:
            conn.execute('UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE id = ?', 
                        (session['user_id'],))
            conn.commit()
        except Exception as e:
            print(f"Ошибка при обновлении активности: {e}")
        finally:
            conn.close()

# ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================

def is_safe_url(target):
    """Проверка безопасности URL для перенаправления"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

# Инициализация базы данных при запуске
init_db()

# Обертываем Flask приложение в ASGI совместимую обертку для Uvicorn
flask_app = app
if ASGI_COMPATIBLE:
    app = WsgiToAsgi(flask_app)

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 IDQR СИСТЕМА ЗАПУЩЕНА!")
    print("="*60)
    print(f"📍 Главная страница: http://localhost:5000")
    print(f"🔑 Админ панель: http://localhost:5000/admin")
    print(f"👤 Тестовый администратор: admin / admin123")
    print(f"📊 База данных: {DATABASE_PATH}")
    print(f"📁 Загрузки: {app.config['UPLOAD_FOLDER']}")
    print("="*60)
    print("📋 Доступные маршруты:")
    print("  • / - Главная страница")
    print("  • /about - О компании")
    print("  • /contacts - Контакты")
    print("  • /portfolio - Портфолио")
    print("  • /blog - Блог")
    print("  • /contact-form - Обратная связь")
    print("  • /login - Вход")
    print("  • /register - Регистрация")
    print("  • /modules - Все модули")
    print("  • /user/dashboard - Панель управления пользователя")
    print("  • /energy - Энергетика")
    print("  • /medicine - Медицина")
    print("  • /business - Бизнес")
    print("  • /services - Услуги")
    print("  • /user/energy - Модуль энергетики")
    print("  • /user/medicine - Модуль медицины")
    print("  • /user/business - Модуль бизнеса")
    print("  • /user/services - Модуль услуг")
    print("="*60)
    print("⚙️  Режим отладки: ВКЛЮЧЕН")
    print("="*60 + "\n")
    
    # Запускаем приложение
    flask_app.run(host='0.0.0.0', port=5000, debug=True)
