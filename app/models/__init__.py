# 这个文件可以为空，或者用于导出模型
from .user import User
from .file import File
from .vocabulary import Vocabulary, Phrase, Sentence, PhonicsRelatedWord

__all__ = ['User', 'File',
            'Vocabulary', 'Phrase', 'Sentence', 'PhonicsRelatedWord']