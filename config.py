# config.py
from decouple import config, Csv

class Config:
    # 基础配置
    DEBUG = config('DEBUG', default=False, cast=bool)
    
    # 数据库配置
    DB_ENGINE = config('DB_ENGINE', default='postgresql')
    DB_NAME = config('DB_NAME')
    DB_USER = config('CMTOOL_AGENT_DB_USER')
    DB_PASSWORD = config('CMTOOL_AGENT_DB_PASSWORD')
    DB_HOST = config('CMTOOL_AGENT_DB_HOST', default='localhost')
    DB_PORT = config('CMTOOL_AGENT_DB_PORT', default='5432', cast=int)
    print(f"Database Config - ENGINE: {DB_ENGINE}, HOST: {DB_HOST}, PORT: {DB_PORT}, NAME: {DB_NAME}, USER: {DB_USER}")
    # 其他常用配置
    ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost', cast=Csv())

# 可选：定义不同环境配置
class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    # 生产环境可以覆盖某些配置
    # SECRET_KEY = config('SECRET_KEY')  # 必须在环境变量中设置

# 根据环境变量选择配置
environment = config('ENVIRONMENT', default='development')

if environment == 'production':
    app_config = ProductionConfig()
elif environment == 'development':
    app_config = DevelopmentConfig()
else:
    app_config = Config()