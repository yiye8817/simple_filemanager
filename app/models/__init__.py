# 这个文件可以为空，或者用于导出模型
from .user import User
from .file import File
from .vocabulary import Vocabulary, Phrase, Sentence, PhonicsRelatedWord
from .resource import Resource
from .version_model import FileVersion
#step 1 添加数据库到models中同步修改config配置，增加数据库（创建）
#需要修改orm的类定义的db和类引用的数据库

__all__ = ['User', 'File',
            'Vocabulary', 'Phrase', 'Sentence', 'PhonicsRelatedWord',
            #添加数据库的类到这里
            'Resource','FileVersion'
            ]
