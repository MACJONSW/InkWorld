"""
异步任务中心引擎
"""
import threading
import traceback
from datetime import datetime


class JobEngine:
    def __init__(self, db):
        self.db = db
        self._threads = {}      # job_id -> Thread
        self._cancel_events = {} # job_id -> threading.Event
        self._job_callbacks = {} # job_type -> callable

    def register_handler(self, job_type, handler):
        """Register a handler function for a job type.
        Handler signature: handler(job_id, job_data, progress_callback, cancel_event)
        """
        self._job_callbacks[job_type] = handler

    def create_job(self, user_id, job_type, book_id=None, total_steps=0):
        """Create a new async job and return its ID."""
        job_id = self.db.create_async_job({
            'user_id': user_id,
            'book_id': book_id,
            'job_type': job_type,
            'total_steps': total_steps
        })
        return job_id

    def start_job(self, job_id):
        """Start executing a job in a background thread."""
        job = self.db.get_async_job(job_id)
        if not job:
            return False

        cancel_event = threading.Event()
        self._cancel_events[job_id] = cancel_event

        def _run():
            try:
                self.db.update_async_job(job_id, {
                    'status': 'running',
                    'started_at': datetime.now().isoformat()
                })
                self.db.add_job_log(job_id, 'info', f'任务开始: {job["job_type"]}')

                handler = self._job_callbacks.get(job['job_type'])
                if not handler:
                    raise ValueError(f'未注册的任务类型: {job["job_type"]}')

                def progress_callback(current_step, total_steps=None, message=None):
                    if cancel_event.is_set():
                        raise InterruptedError('任务已取消')
                    updates = {'current_step': current_step}
                    if total_steps is not None:
                        updates['total_steps'] = total_steps
                    if total_steps:
                        updates['progress'] = int(current_step / total_steps * 100)
                    self.db.update_async_job(job_id, updates)
                    if message:
                        self.db.add_job_log(job_id, 'info', message)

                result = handler(job_id, job, progress_callback, cancel_event)

                if cancel_event.is_set():
                    self.db.update_async_job(job_id, {
                        'status': 'cancelled',
                        'completed_at': datetime.now().isoformat()
                    })
                    self.db.add_job_log(job_id, 'warning', '任务已取消')
                else:
                    self.db.update_async_job(job_id, {
                        'status': 'completed',
                        'progress': 100,
                        'result_summary': str(result or '完成'),
                        'completed_at': datetime.now().isoformat()
                    })
                    self.db.add_job_log(job_id, 'info', '任务完成')

            except InterruptedError:
                self.db.update_async_job(job_id, {
                    'status': 'cancelled',
                    'completed_at': datetime.now().isoformat()
                })
                self.db.add_job_log(job_id, 'warning', '任务已取消')
            except Exception as e:
                self.db.update_async_job(job_id, {
                    'status': 'failed',
                    'error_message': str(e),
                    'completed_at': datetime.now().isoformat()
                })
                self.db.add_job_log(job_id, 'error', f'任务失败: {traceback.format_exc()}')
            finally:
                self._threads.pop(job_id, None)
                self._cancel_events.pop(job_id, None)

        t = threading.Thread(target=_run, daemon=True)
        self._threads[job_id] = t
        t.start()
        return True

    def cancel_job(self, job_id):
        """Cancel a running job."""
        ev = self._cancel_events.get(job_id)
        if ev:
            ev.set()
            return True
        return False

    def retry_job(self, job_id):
        """Retry a failed job by creating a new one with same params."""
        old_job = self.db.get_async_job(job_id)
        if not old_job or old_job['status'] not in ('failed', 'cancelled'):
            return None
        new_id = self.create_job(
            old_job['user_id'], old_job['job_type'],
            book_id=old_job.get('book_id'),
            total_steps=old_job.get('total_steps', 0)
        )
        self.start_job(new_id)
        return new_id

    def get_running_jobs(self, user_id):
        """Get all running jobs for a user."""
        return self.db.get_async_jobs(user_id, status='running')

    def create_and_start(self, user_id, job_type, book_id=None, total_steps=0):
        """Convenience: create + start in one call."""
        job_id = self.create_job(user_id, job_type, book_id, total_steps)
        self.start_job(job_id)
        return job_id
