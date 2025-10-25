from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select
from .db import engine, Monitor
from .db import cleanup_old_results

scheduler = BackgroundScheduler()

def schedule_monitor(mon: Monitor, run_fn):
    freq = max(5, int(mon.frequency_minutes))
    scheduler.add_job(
        run_fn,
        trigger=IntervalTrigger(minutes=freq),
        args=[mon.id],
        id=f"monitor-{mon.id}",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

def load_schedules(run_fn):
    with Session(engine) as session:
        mons = session.exec(select(Monitor).where(Monitor.is_active == True)).all()
        for m in mons:
            schedule_monitor(m, run_fn)
            print(f"[Scheduler] Loaded monitor {m.id} → every {m.frequency_minutes} min")

# housekeeping
scheduler.add_job(cleanup_old_results, 'interval', days=3)
print("[Scheduler] Added cleanup job (every 3 days)")
