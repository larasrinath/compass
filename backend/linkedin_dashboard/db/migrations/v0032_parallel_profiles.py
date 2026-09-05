"""Allow two profile reads; all other jobs still own the browser exclusively."""

from sqlalchemy import Connection

VERSION = "0032_parallel_profiles"
TRIGGER_NAMES = ("profile_slots_insert", "profile_slots_update")
INDEX_NAMES = ("one_running_job",)


def apply(connection: Connection) -> None:
    connection.exec_driver_sql("DROP INDEX IF EXISTS one_running_job")
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX one_running_job ON job ((1)) "
        "WHERE state = 'running' AND kind <> 'get_person_profile'"
    )
    for operation in ("INSERT", "UPDATE"):
        connection.exec_driver_sql(f"""
            CREATE TRIGGER profile_slots_{operation.lower()}
            BEFORE {operation} ON job
            WHEN NEW.state = 'running' AND (
                (SELECT count(*) FROM job WHERE state = 'running'
                 AND id <> NEW.id) >= 2
                OR EXISTS (SELECT 1 FROM job WHERE state = 'running'
                    AND id <> NEW.id AND (
                        kind <> 'get_person_profile'
                        OR NEW.kind <> 'get_person_profile'
                        OR claim_token <> NEW.claim_token))
            )
            BEGIN SELECT RAISE(ABORT, 'one_running_job: browser slots occupied'); END
        """)
