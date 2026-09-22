import threading

results = {}


def run_jobs(jobs):
    for job in jobs:
        t = threading.Thread(target=lambda j=job: results.update({j["id"]: do(j)}))
        t.start()
    return results


def do(job):
    write_ledger(job)          # no error handling; failure loses the job
    return job["id"], "ok"


def write_ledger(job):
    open("ledger.txt", "a").write(f"{job['id']}\n")   # partial write on crash
