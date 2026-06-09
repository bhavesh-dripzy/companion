"""Knowledge-graph / leadgen extraction package (runs on the graph EC2 worker).

Relocated from `app/services/leadgen/` in the legacy teardown. Imported only by
the EC2 graph worker (`deploy/graph-ec2/worker.py` + `graph_service.py`), which
bundles the whole repo and adds the repo root to `sys.path`; the API Lambda only
enqueues turn events to SQS (`companion_api.leadgen_queue`) and never imports this.
"""
