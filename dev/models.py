from django.db import models


def _hours(default, help_text):
    # Hours to the hundredth: a half day of 7.2 h is 3.6.
    return models.DecimalField(max_digits=6, decimal_places=2, default=default, help_text=help_text)


class Dev(models.Model):
    name = models.CharField(max_length=100)
    jira_username = models.CharField(
        max_length=100,
        blank=True,
        help_text="Jira username (e.g. TDPTAV): matches worklogs and assignees exactly, instead of by name",
    )
    workload = _hours(36, 'Hours of workloads per sprint')
    holidays = _hours(0, 'Hours of holidays per sprint')
    fastlane = _hours(7, 'Hours of planned fastlane per sprint')
    project_meetings = _hours(0, 'Hours of project meetings per sprint')
    other_meetings = _hours(0, 'Hours of other meetings per sprint')
    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Dev'
        verbose_name_plural = 'Devs'
        ordering = ("name",)


