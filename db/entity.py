from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, Boolean, Text, DateTime, Integer, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid

Base = declarative_base()


class UserSession(Base):
    """用户会话表"""
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(256), nullable=False)
    session_name = Column(String(256))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('user_id', 'session_id', name='uq_user_session'),
    )


class AgentConfig(Base):
    """Agent 配置表"""
    __tablename__ = "agent_configs"

    user_id = Column(String(64), primary_key=True)
    session_id = Column(String(256), primary_key=True)
    config = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
