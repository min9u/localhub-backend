import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, Cookie, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Post, PostLike
from app.schemas import PostCreate, PostUpdate
from app.responses import success_response
from app.utils import new_uuid, now_iso, hash_password, verify_password

router = APIRouter(prefix="/posts", tags=["posts"])


def post_to_dict(post, like_count, liked=None):
    """DB의 Post 객체를 명세의 camelCase 응답 형식으로 변환"""
    data = {
        "id": post.id,
        "time": post.created_at,
        "title": post.title,
        "content": post.content,
        "viewCount": post.view_count,   # DB의 view_count → 응답은 viewCount
        "likeCount": like_count,
    }
    if liked is not None:
        data["liked"] = liked
    return data


# ── 1. 게시글 목록 조회 ─────────────────────────────
@router.get("")
def list_posts(
    page: int = Query(1, ge=1),
    take: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    total = db.query(Post).count()
    total_pages = math.ceil(total / take) if total else 0

    posts = (
        db.query(Post)
        .order_by(Post.created_at.desc())   # 최신순
        .offset((page - 1) * take)          # 앞의 페이지들 건너뛰기
        .limit(take)                        # take개만 가져오기
        .all()
    )

    # 좋아요 개수를 한 번의 쿼리로 {post_id: 개수} 형태로 모아둠
    like_counts = dict(
        db.query(PostLike.post_id, func.count(PostLike.id))
        .group_by(PostLike.post_id)
        .all()
    )

    items = [post_to_dict(p, like_counts.get(p.id, 0)) for p in posts]

    return success_response(
        data={
            "items": items,
            "meta": {
                "page": page,
                "take": take,
                "total": total,
                "totalPages": total_pages,
            },
        },
        message="게시글 목록 조회에 성공했습니다.",
    )


# ── 2. 게시글 상세 조회 ─────────────────────────────
@router.get("/{post_id}")
def get_post(
    post_id: str,
    db: Session = Depends(get_db),
    client_id: Optional[str] = Cookie(default=None),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")

    # 조회할 때마다 조회수 1 증가
    post.view_count += 1
    db.commit()
    db.refresh(post)

    like_count = db.query(PostLike).filter(PostLike.post_id == post_id).count()

    # 이 사용자가 좋아요를 눌렀는지 여부
    liked = False
    if client_id:
        liked = (
            db.query(PostLike)
            .filter(PostLike.post_id == post_id, PostLike.client_id == client_id)
            .first()
            is not None
        )

    return success_response(
        data=post_to_dict(post, like_count, liked=liked),
        message="게시글 조회에 성공했습니다.",
    )


# ── 3. 게시글 작성 ─────────────────────────────────
@router.post("", status_code=201)
def create_post(payload: PostCreate, db: Session = Depends(get_db)):
    now = now_iso()
    post = Post(
        id=new_uuid(),
        created_at=now,
        updated_at=now,
        title=payload.title,
        content=payload.content,
        password_hash=hash_password(payload.pwd),   # 해시해서 저장
        view_count=0,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    return success_response(
        data=post_to_dict(post, like_count=0),
        message="게시글 작성에 성공했습니다.",
    )


# ── 4. 게시글 수정 ─────────────────────────────────
@router.patch("/{post_id}")
def update_post(post_id: str, payload: PostUpdate, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")

    # 비밀번호 확인
    if not verify_password(payload.pwd, post.password_hash):
        raise HTTPException(status_code=403, detail="비밀번호가 일치하지 않습니다.")

    post.title = payload.title
    post.content = payload.content
    post.updated_at = now_iso()
    db.commit()
    db.refresh(post)

    like_count = db.query(PostLike).filter(PostLike.post_id == post_id).count()

    return success_response(
        data=post_to_dict(post, like_count),
        message="게시글 수정에 성공했습니다.",
    )


# ── 5. 좋아요 등록 ─────────────────────────────────
@router.post("/{post_id}/likes")
def like_post(
    post_id: str,
    response: Response,
    db: Session = Depends(get_db),
    client_id: Optional[str] = Cookie(default=None),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")

    # 쿠키에 clientId가 없으면 새로 발급해서 쿠키에 심어줌
    if not client_id:
        client_id = new_uuid()
        response.set_cookie(
            key="client_id",
            value=client_id,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 365,   # 1년
        )

    # 이미 눌렀는지 확인 (중복 방지)
    existing = (
        db.query(PostLike)
        .filter(PostLike.post_id == post_id, PostLike.client_id == client_id)
        .first()
    )
    if not existing:
        like = PostLike(
            id=new_uuid(),
            post_id=post_id,
            client_id=client_id,
            created_at=now_iso(),
        )
        db.add(like)
        db.commit()

    like_count = db.query(PostLike).filter(PostLike.post_id == post_id).count()

    return success_response(
        data={"postId": post_id, "liked": True, "likeCount": like_count},
        message="좋아요가 등록되었습니다.",
    )


# ── 6. 좋아요 취소 ─────────────────────────────────
@router.delete("/{post_id}/likes")
def unlike_post(
    post_id: str,
    db: Session = Depends(get_db),
    client_id: Optional[str] = Cookie(default=None),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")

    if client_id:
        like = (
            db.query(PostLike)
            .filter(PostLike.post_id == post_id, PostLike.client_id == client_id)
            .first()
        )
        if like:
            db.delete(like)
            db.commit()

    like_count = db.query(PostLike).filter(PostLike.post_id == post_id).count()

    return success_response(
        data={"postId": post_id, "liked": False, "likeCount": like_count},
        message="좋아요가 취소되었습니다.",
    )