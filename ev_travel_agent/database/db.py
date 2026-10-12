from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, JSON, DateTime
import datetime

DATABASE_URL = "sqlite+aiosqlite:///./ev_travel.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()


class TripRecord(Base):
    __tablename__ = "trips"
    id = Column(Integer, primary_key=True, index=True)
    origin = Column(String)
    destination = Column(String)
    battery_level = Column(Integer)
    preferences = Column(String)
    plan = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class UserPreference(Base):
    __tablename__ = "preferences"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True)
    fav_restaurants = Column(JSON, default=[])
    fav_charging_networks = Column(JSON, default=[])


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
