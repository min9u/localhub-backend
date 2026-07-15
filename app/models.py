from sqlalchemy import (
    Column, String, Integer, Float,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base


class Post(Base):
    __tablename__ = "posts"

    id = Column(String, primary_key=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    view_count = Column(Integer, nullable=False, default=0)

    # 이 게시글에 달린 좋아요들 (파이썬에서 post.likes 로 접근 가능)
    likes = relationship(
        "PostLike", back_populates="post", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # 목록 조회 시 최신순 정렬을 빠르게 하기 위한 인덱스
        Index("idx_posts_created_at", created_at.desc()),
    )


class PostLike(Base):
    __tablename__ = "post_likes"

    id = Column(String, primary_key=True)
    post_id = Column(
        String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    client_id = Column(String, nullable=False)
    created_at = Column(String, nullable=False)

    post = relationship("Post", back_populates="likes")

    __table_args__ = (
        # 같은 사람이 같은 글에 좋아요 두 번 못 누르게 막는 제약
        UniqueConstraint("post_id", "client_id", name="uq_post_client"),
        Index("idx_post_likes_post_id", "post_id"),
    )


class Place(Base):
    __tablename__ = "places"

    id = Column(String, primary_key=True)
    content_id = Column(String, nullable=False, unique=True)
    content_type_id = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    address = Column(String)              # nullable=False 안 붙이면 기본이 NULL 허용
    first_image_url = Column(String)
    map_x = Column(Float)                 # SQLite의 REAL = SQLAlchemy의 Float
    map_y = Column(Float)

    __table_args__ = (
        Index("idx_places_content_type_id", "content_type_id"),
    )


class Contest(Base):
    __tablename__ = "contests"

    id = Column(String, primary_key=True)
    place_id = Column(
        String, ForeignKey("places.id", ondelete="SET NULL")
    )
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    title = Column(String, nullable=False)
    image_url = Column(String)
    description = Column(String)
    age_limit = Column(String)

    place = relationship("Place")

    __table_args__ = (
        Index("idx_contests_date", "start_date", "end_date"),
    )