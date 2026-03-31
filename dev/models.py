from django.db import models


class Dev(models.Model):
    name = models.CharField(max_length=100)
    workload = models.IntegerField(default=36, help_text='Hours of workloads per sprint')
    holidays = models.IntegerField(default=0, help_text='Hours of holidays per sprint')
    fastlane = models.IntegerField(default=7, help_text='Hours of planned fastlane per sprint')
    project_meetings = models.IntegerField(default=0, help_text='Hours of project meetings per sprint')
    other_meetings = models.IntegerField(default=0, help_text='Hours of other meetings per sprint')
    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Dev'
        verbose_name_plural = 'Devs'
        ordering = ("name",)


