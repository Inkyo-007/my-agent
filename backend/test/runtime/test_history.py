"""runtime/history.py 的离线单元测试"""

from src.core import Message, MessageRole, MessageType
from src.runtime import MessageHistory


def user_msg(content: str = "hi") -> Message:
    return Message(role=MessageRole.USER, type=MessageType.TEXT, content=content)


class Test追加与读取:
    def test_追加后按序可读(self):
        history = MessageHistory()
        history.append(user_msg("第一条"))
        history.append(user_msg("第二条"))
        assert [m.content for m in history.messages] == ["第一条", "第二条"]

    def test_messages返回副本(self):
        """外部修改返回的列表不影响内部状态"""
        history = MessageHistory()
        history.append(user_msg())
        history.messages.clear()
        assert len(history.messages) == 1

    def test_初始历史用于会话恢复(self):
        history = MessageHistory([user_msg("旧消息")])
        history.append(user_msg("新消息"))
        assert [m.content for m in history.messages] == ["旧消息", "新消息"]

    def test_初始列表被拷贝(self):
        """构造后外部修改源列表不影响历史"""
        seed = [user_msg("旧消息")]
        history = MessageHistory(seed)
        seed.clear()
        assert len(history.messages) == 1


class Test协议转换:
    def test_to_provider_messages委托转换层(self):
        history = MessageHistory([user_msg("你好")])
        (pm,) = history.to_provider_messages()
        assert pm.role == "user"
        assert pm.content == "你好"
