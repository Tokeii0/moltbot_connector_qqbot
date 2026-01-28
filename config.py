"""
Moltbot Connector 配置
"""
from pydantic import BaseModel, ConfigDict, field_validator


class Config(BaseModel):
    """Moltbot 连接配置"""
    
    model_config = ConfigDict(extra="ignore")
    
    # Moltbot Gateway WebSocket URL
    moltbot_gateway_url: str = "ws://127.0.0.1:18789"
    
    # 认证 token（可选）
    moltbot_token: str | None = None
    
    # 认证密码（可选）
    moltbot_password: str | None = None
    
    # 客户端名称（必须是 Moltbot 预定义的 ID）
    moltbot_client_name: str = "gateway-client"
    
    # 客户端版本
    moltbot_client_version: str = "1.0.0"
    
    # 请求超时时间（秒）
    moltbot_timeout: int = 60000
    
    # 是否启用调试日志
    moltbot_debug: bool = False
    
    # 允许使用的 QQ 号白名单（空列表表示允许所有人）
    moltbot_allowed_users: list[int] = []
    
    @field_validator('moltbot_allowed_users', mode='before')
    @classmethod
    def parse_allowed_users(cls, v):
        """解析白名单配置，支持逗号分隔的字符串或单个整数"""
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(',') if x.strip()]
        elif isinstance(v, int):
            # 单个整数转为列表
            return [v]
        elif isinstance(v, list):
            return v
        return []
