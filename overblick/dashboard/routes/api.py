"""
API routes — JSON health endpoint.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint (public, no auth required)."""
    return {
        "status": "healthy",
        "service": "overblick-dashboard",
        "version": "0.1.0",
    }
