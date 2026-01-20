from django.db import models
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password
from django.db import models

class ClassGroup(models.Model):
    name = models.CharField(max_length=32, unique=True)  # например "7A"
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Class"
        verbose_name_plural = "Classes"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Student(models.Model):
    full_name = models.CharField(max_length=120)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="students")
    pin_hash = models.CharField(max_length=256)  # хэш 6-значного кода
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Student"
        verbose_name_plural = "Students"
        constraints = [
            models.UniqueConstraint(fields=["class_group", "full_name"], name="uniq_student_in_class")
        ]
        indexes = [
            models.Index(fields=["class_group", "full_name"]),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.class_group})"

    def set_pin(self, pin: str) -> None:
        # pin ожидаем строкой из 6 цифр; валидацию лучше делать в формах/serializer'ах
        self.pin_hash = make_password(pin)

    def check_pin(self, pin: str) -> bool:
        return check_password(pin, self.pin_hash)


class Session(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        CLOSED = "closed", "Closed"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    # по ТЗ: по времени Астаны — в settings TIME_ZONE = Asia/Almaty.
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    # Какие классы имеют доступ
    allowed_classes = models.ManyToManyField(ClassGroup, through="SessionClass", related_name="sessions")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Session"
        verbose_name_plural = "Sessions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "starts_at", "ends_at"]),
        ]

    def __str__(self):
        return f"{self.title} [{self.status}]"

    def is_active_now(self) -> bool:
        """Активна ли сессия по времени и статусу."""
        if self.status != self.Status.RUNNING:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class SessionClass(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT)

    class Meta:
        verbose_name = "Session access"
        verbose_name_plural = "Session access"
        constraints = [
            models.UniqueConstraint(fields=["session", "class_group"], name="uniq_session_class")
        ]

    def __str__(self):
        return f"{self.session} -> {self.class_group}"


class SessionTask(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="tasks")
    position = models.PositiveIntegerField()  # порядок в меню слева

    title = models.CharField(max_length=200)
    statement = models.TextField()            # описание задачи
    constraints = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Task"
        verbose_name_plural = "Tasks"
        ordering = ["session", "position"]
        constraints = [
            models.UniqueConstraint(fields=["session", "position"], name="uniq_task_position_in_session")
        ]
        indexes = [
            models.Index(fields=["session", "position"]),
        ]

    def __str__(self):
        return f"[{self.session_id}] {self.position}. {self.title}"


class TaskTestCase(models.Model):
    task = models.ForeignKey(SessionTask, on_delete=models.CASCADE, related_name="testcases")
    ordinal = models.PositiveIntegerField()

    stdin = models.TextField()
    expected_stdout = models.TextField()
    is_visible = models.BooleanField(default=False)  # показывать ученику (1-2 примера)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Test case"
        verbose_name_plural = "Test cases"
        ordering = ["task", "ordinal"]
        constraints = [
            models.UniqueConstraint(fields=["task", "ordinal"], name="uniq_testcase_ordinal_in_task")
        ]
        indexes = [
            models.Index(fields=["task", "is_visible"]),
        ]

    def __str__(self):
        return f"Task {self.task_id} test #{self.ordinal} ({'visible' if self.is_visible else 'hidden'})"


class StudentSession(models.Model):
    class FinishReason(models.TextChoices):
        COMPLETED = "completed", "Completed"
        TIMEOUT = "timeout", "Timeout"
        MANUAL = "manual", "Manual close"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="student_sessions")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="student_sessions")

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    finish_reason = models.CharField(max_length=16, choices=FinishReason.choices, null=True, blank=True)
    last_submit_at = models.DateTimeField(null=True, blank=True)
    last_code_hash = models.CharField(max_length=64, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Student session"
        verbose_name_plural = "Student sessions"
        constraints = [
            models.UniqueConstraint(fields=["student", "session"], name="uniq_student_session")
        ]
        indexes = [
            models.Index(fields=["session",'student']),
        ]

    def __str__(self):
        return f"{self.student} @ {self.session}"


class StudentTaskProgress(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        SOLVED = "solved", "Solved"
        LOCKED = "locked", "Locked"

    student_session = models.ForeignKey(StudentSession, on_delete=models.CASCADE, related_name="task_progress")
    task = models.ForeignKey(SessionTask, on_delete=models.CASCADE, related_name="progress_records")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NOT_STARTED)
    opened_at = models.DateTimeField(null=True, blank=True)
    solved_at = models.DateTimeField(null=True, blank=True)

    attempts_total = models.PositiveIntegerField(default=0)
    attempts_failed = models.PositiveIntegerField(default=0)

    hint1_unlocked_at = models.DateTimeField(null=True, blank=True)
    hint2_unlocked_at = models.DateTimeField(null=True, blank=True)
    hint1_text = models.TextField(blank=True)
    hint2_text = models.TextField(blank=True)
    last_submit_at = models.DateTimeField(null=True, blank=True)
    last_code_hash = models.CharField(max_length=64, blank=True, default="")

    locked_after_solve = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Task progress"
        verbose_name_plural = "Task progress"
        constraints = [
            models.UniqueConstraint(fields=["student_session", "task"], name="uniq_progress_student_task")
        ]
        indexes = [
            models.Index(fields=["student_session", "task"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.student_session} -> {self.task} ({self.status})"

    def mark_opened(self):
        if not self.opened_at:
            self.opened_at = timezone.now()
        if self.status == self.Status.NOT_STARTED:
            self.status = self.Status.IN_PROGRESS

    def mark_solved(self):
        self.status = self.Status.SOLVED
        self.solved_at = timezone.now()
        if self.locked_after_solve:
            # “недоступна для просмотра” — можно в API/логике скрывать statement
            pass


class Submission(models.Model):
    class Verdict(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        WRONG_ANSWER = "wrong_answer", "Wrong Answer"
        TIME_LIMIT = "time_limit", "Time Limit Exceeded"
        COMPILATION_ERROR = "compilation_error", "Compilation Error"
        RUNTIME_ERROR = "runtime_error", "Runtime Error"

    progress = models.ForeignKey("StudentTaskProgress", on_delete=models.CASCADE, related_name="submissions")

    attempt_no = models.PositiveIntegerField()
    code = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)

    verdict = models.CharField(max_length=32, choices=Verdict.choices)

    stdout = models.TextField(blank=True)
    stderr = models.TextField(blank=True)

    passed_tests = models.PositiveIntegerField(default=0)
    total_tests = models.PositiveIntegerField(default=0)

    external_run_id = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["progress", "attempt_no"], name="uniq_attempt_no_per_progress")
        ]

    class Meta:
        verbose_name = "Submission"
        verbose_name_plural = "Submissions"
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(fields=["progress", "attempt_no"], name="uniq_attempt_no_per_progress")
        ]
        indexes = [
            models.Index(fields=["progress", "attempt_no"]),
            models.Index(fields=["submitted_at"]),
        ]

    def __str__(self):
        return f"Submission {self.id} ({self.verdict})"


class ActivityEvent(models.Model):
    class Type(models.TextChoices):
        COPY = "copy", "Copy"
        PASTE = "paste", "Paste"
        TAB_HIDDEN = "tab_hidden", "Tab hidden"
        TAB_VISIBLE = "tab_visible", "Tab visible"
        FOCUS_LOST = "focus_lost", "Focus lost"
        FOCUS_GAINED = "focus_gained", "Focus gained"
        OPEN_TASK = "open_task", "Open task"
        SUBMIT = "submit", "Submit"

    progress = models.ForeignKey(StudentTaskProgress, on_delete=models.CASCADE, related_name="activity_events")
    occurred_at = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=32, choices=Type.choices)
    payload = models.JSONField(default=dict, blank=True)  # длина вставки, имя вкладки и т.п.

    class Meta:
        verbose_name = "Activity event"
        verbose_name_plural = "Activity events"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["progress", "occurred_at"]),
            models.Index(fields=["event_type"]),
        ]

    def __str__(self):
        return f"{self.event_type} @ {self.occurred_at}"


class ActivityAggregate(models.Model):
    progress = models.OneToOneField(StudentTaskProgress, on_delete=models.CASCADE, related_name="activity_agg")

    total_copies = models.PositiveIntegerField(default=0)
    total_pastes = models.PositiveIntegerField(default=0)
    tab_switches = models.PositiveIntegerField(default=0)
    focus_lost_count = models.PositiveIntegerField(default=0)

    active_time_seconds = models.PositiveIntegerField(default=0)

    # NEW: hint requests counters
    hint1_requests = models.PositiveIntegerField(default=0)
    hint2_requests = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)
# core/models.py
from django.db import models

class AiAssistMessage(models.Model):
    class Status(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"

    progress = models.ForeignKey(
        "StudentTaskProgress",
        on_delete=models.CASCADE,
        related_name="ai_messages",
        db_index=True,
    )

    level = models.PositiveSmallIntegerField()  # 1 или 2

    prompt_snapshot = models.TextField()
    response_text = models.TextField(blank=True)

    model = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    tokens_in = models.PositiveIntegerField(null=True, blank=True)
    tokens_out = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["progress", "level", "created_at"])]

    def __str__(self):
        return f"AiAssistMessage(progress={self.progress_id}, level={self.level}, status={self.status})"
