"""通知渠道基类。所有通知渠道必须继承此类。"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class NotificationChannel(ABC):
    """通知渠道抽象基类"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """渠道名称"""
        ...

    @abstractmethod
    def is_enabled(self) -> bool:
        """检查此渠道是否启用"""
        ...

    @abstractmethod
    def send(self, title: str, message: str) -> bool:
        """发送通知。返回 True 表示成功。"""
        ...
