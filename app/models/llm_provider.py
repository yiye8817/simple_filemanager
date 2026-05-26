"""大模型 (LLM) 提供商配置：按类别保存 base_url / api_key / 默认模型 / 参考链接。"""
import json
from datetime import datetime

from app import db


class LLMProvider(db.Model):
    __tablename__ = 'llm_provider'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)          # 配置显示名
    category = db.Column(db.String(50), nullable=False, default='其他')  # 分类：OpenAI、Anthropic、国产、本地、其他
    provider = db.Column(db.String(50))                       # 厂商标识 openai/anthropic/azure/ollama/custom
    base_url = db.Column(db.String(500))
    api_key = db.Column(db.String(500))
    default_model = db.Column(db.String(100))
    reference_links = db.Column(db.Text, default='[]')        # JSON 数组 [{title,url}]
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'name', name='uq_llm_user_name'),
    )

    # ---------- 参考链接：以 JSON 文本存储 [{title,url}] ----------
    def get_reference_links(self):
        try:
            data = json.loads(self.reference_links or '[]')
        except (TypeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        out = []
        for it in data:
            if isinstance(it, dict) and it.get('url'):
                out.append({
                    'title': str(it.get('title') or it.get('url')),
                    'url': str(it.get('url')),
                })
            elif isinstance(it, str) and it.strip():
                out.append({'title': it.strip(), 'url': it.strip()})
        return out

    def set_reference_links(self, items):
        cleaned = []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    url = str(it.get('url') or '').strip()
                    if not url:
                        continue
                    title = str(it.get('title') or url).strip()
                    cleaned.append({'title': title, 'url': url})
                elif isinstance(it, str):
                    u = it.strip()
                    if u:
                        cleaned.append({'title': u, 'url': u})
        self.reference_links = json.dumps(cleaned, ensure_ascii=False)

    @staticmethod
    def _mask(value):
        s = value or ''
        if not s:
            return ''
        if len(s) <= 8:
            return '*' * len(s)
        return s[:4] + '*' * (len(s) - 8) + s[-4:]

    def to_dict(self, *, mask_key=False):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'category': self.category or '其他',
            'provider': self.provider,
            'base_url': self.base_url,
            'api_key': self._mask(self.api_key) if mask_key else (self.api_key or ''),
            'default_model': self.default_model,
            'reference_links': self.get_reference_links(),
            'notes': self.notes,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None,
        }
