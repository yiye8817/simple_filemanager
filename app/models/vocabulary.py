from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
# 明确重命名relationship函数以避免冲突
from sqlalchemy.orm import relationship as orm_relationship
# from database import db
import datetime
from app import db
class Vocabulary(db.Model):
    __bind_key__ = 'vocabulary'  # 指定使用vocabulary数据库
    __tablename__ = 'vocabulary'
    
    id = Column(Integer, primary_key=True)
    frequency_rank = Column(Integer, nullable=False, unique=True, index=True)
    letter_count = Column(Integer)
    level = Column(String(10))
    alpha_order = Column(Integer)
    english = Column(String(100), nullable=False)
    pronunciation = Column(String(100))
    chinese = Column(String(200))
    part_of_speech = Column(String(50))
    category = Column(String(50))
    memory_method = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # 关联关系
    phrases = relationship("Phrase", back_populates="vocabulary", cascade="all, delete-orphan", lazy='select')
    sentences = relationship("Sentence", back_populates="vocabulary", cascade="all, delete-orphan", lazy='select')
    phonics_words = relationship("PhonicsRelatedWord", back_populates="vocabulary", cascade="all, delete-orphan", lazy='select')
    
    __table_args__ = (
        Index('idx_english', 'english'),
        Index('idx_frequency', 'frequency_rank'),
        Index('idx_level', 'level'),
        Index('idx_category', 'category'),
    )
    
    def to_dict(self, include_relations=True):
        result = {
            'id': self.id,
            'frequency_rank': self.frequency_rank,
            'letter_count': self.letter_count,
            'level': self.level,
            'alpha_order': self.alpha_order,
            'english': self.english,
            'pronunciation': self.pronunciation,
            'chinese': self.chinese,
            'part_of_speech': self.part_of_speech,
            'category': self.category,
            'memory_method': self.memory_method,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
        
        if include_relations:
            result.update({
                'phrases': [phrase.to_dict() for phrase in self.phrases],
                'sentences': [sentence.to_dict() for sentence in self.sentences],
                'phonics_words': [word.to_dict() for word in self.phonics_words]
            })
        
        return result

class Phrase(db.Model):
    __bind_key__ = 'vocabulary'
    __tablename__ = 'phrases'
    
    id = Column(Integer, primary_key=True)
    vocabulary_id = Column(Integer, ForeignKey('vocabulary.id', ondelete='CASCADE'))
    phrase = Column(Text, nullable=False)
    chinese = Column(Text)
    
    vocabulary = relationship("Vocabulary", back_populates="phrases")
    
    def to_dict(self):
        return {
            'id': self.id,
            'phrase': self.phrase,
            'chinese': self.chinese
        }

class Sentence(db.Model):
    __bind_key__ = 'vocabulary'
    __tablename__ = 'sentences'
    
    id = Column(Integer, primary_key=True)
    vocabulary_id = Column(Integer, ForeignKey('vocabulary.id', ondelete='CASCADE'))
    sentence = Column(Text, nullable=False)
    chinese = Column(Text)
    
    vocabulary = relationship("Vocabulary", back_populates="sentences")
    
    def to_dict(self):
        return {
            'id': self.id,
            'sentence': self.sentence,
            'chinese': self.chinese
        }

class PhonicsRelatedWord(db.Model):
    __bind_key__ = 'vocabulary'
    __tablename__ = 'phonics_related_words'
    
    id = Column(Integer, primary_key=True)
    vocabulary_id = Column(Integer, ForeignKey('vocabulary.id', ondelete='CASCADE'))
    related_word = Column(String(100), nullable=False)
    relationship = Column(String(50))
    
    vocabulary = orm_relationship("Vocabulary", back_populates="phonics_words")
    
    def to_dict(self):
        return {
            'id': self.id,
            'related_word': self.related_word,
            'relationship_type': self.relationship
        }