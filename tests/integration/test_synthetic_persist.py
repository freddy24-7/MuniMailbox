from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Case, Citizen, Role, User
from scripts.generate_synthetic import generate, persist


async def test_generated_data_round_trips_through_the_database(session: AsyncSession) -> None:
    dataset = generate(20, 0, seed=11)
    await persist(session, dataset)

    assert await session.scalar(select(func.count()).select_from(Citizen)) == 20
    assert await session.scalar(select(func.count()).select_from(Case)) == len(dataset.cases)

    stored = await session.get(Citizen, dataset.citizens[0].id)
    assert stored is not None
    assert stored.bsn == dataset.citizens[0].bsn

    roles = set((await session.scalars(select(User.role))).all())
    assert roles == set(Role)


async def test_demo_users_are_created_only_once(session: AsyncSession) -> None:
    await persist(session, generate(3, 0, seed=1))
    await persist(session, generate(3, 0, seed=2))

    users = await session.scalar(select(func.count()).select_from(User))
    assert users == len(generate(1, 0, seed=1).users)
