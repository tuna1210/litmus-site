from django.conf.urls import url
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse, reverse_lazy
from django.db import transaction, connection
from django.db.models import TextField, Q
from django.forms import ModelForm, ModelMultipleChoiceField, TextInput
from django.http import HttpResponseRedirect, Http404
from django.shortcuts import get_object_or_404
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _, ugettext, ungettext
from mptt.admin import MPTTModelAdmin
from reversion.admin import VersionAdmin

from judge.admin.comments import CommentAdmin
from judge.admin.problem import ProblemAdmin
from judge.admin.profile import ProfileAdmin
from judge.admin.submission import SubmissionAdmin
from judge.dblock import LockModel
from judge.models import Language, Profile, Problem, ProblemGroup, ProblemType, Submission, Comment, \
    MiscConfig, Judge, NavigationBar, Contest, ContestParticipation, ContestProblem, Organization, BlogPost, \
    Solution, Rating, License, OrganizationRequest, \
    ContestTag
from judge.ratings import rate_contest
from judge.widgets import AdminPagedownWidget, MathJaxAdminPagedownWidget, \
    HeavyPreviewAdminPageDownWidget, HeavySelect2Widget, HeavySelect2MultipleWidget, Select2Widget, Select2MultipleWidget


class HeavySelect2Widget(HeavySelect2Widget):
    @property
    def is_hidden(self):
        return False


# try:
#    from suit.admin import SortableModelAdmin, SortableTabularInline
# except ImportError:
SortableModelAdmin = object
SortableTabularInline = admin.TabularInline


class LanguageForm(ModelForm):
    problems = ModelMultipleChoiceField(
        label=_('Disallowed problems'),
        queryset=Problem.objects.all(),
        required=False,
        help_text=_('These problems are NOT allowed to be submitted in this language'),
        widget=HeavySelect2MultipleWidget(data_view='problem_select2'))


class LanguageAdmin(VersionAdmin):
    fields = ('key', 'name', 'short_name', 'common_name', 'ace', 'pygments', 'info', 'description', 'problems')
    list_display = ('key', 'name', 'common_name', 'info')
    form = LanguageForm

    if AdminPagedownWidget is not None:
        formfield_overrides = {
            TextField: {'widget': AdminPagedownWidget},
        }

    def save_model(self, request, obj, form, change):
        super(LanguageAdmin, self).save_model(request, obj, form, change)
        obj.problem_set = Problem.objects.exclude(id__in=form.cleaned_data['problems'].values('id'))

    def get_form(self, request, obj=None, **kwargs):
        self.form.base_fields['problems'].initial = \
            Problem.objects.exclude(id__in=obj.problem_set.values('id')).values_list('pk', flat=True) if obj else []
        return super(LanguageAdmin, self).get_form(request, obj, **kwargs)


class ProblemGroupForm(ModelForm):
    problems = ModelMultipleChoiceField(
        label=_('Included problems'),
        queryset=Problem.objects.all(),
        required=False,
        help_text=_('These problems are included in this group of problems'),
        widget=HeavySelect2MultipleWidget(data_view='problem_select2'))


class ProblemGroupAdmin(admin.ModelAdmin):
    fields = ('name', 'full_name', 'problems')
    form = ProblemGroupForm

    def save_model(self, request, obj, form, change):
        super(ProblemGroupAdmin, self).save_model(request, obj, form, change)
        obj.problem_set = form.cleaned_data['problems']
        obj.save()

    def get_form(self, request, obj=None, **kwargs):
        self.form.base_fields['problems'].initial = [o.pk for o in obj.problem_set.all()] if obj else []
        return super(ProblemGroupAdmin, self).get_form(request, obj, **kwargs)


class ProblemTypeForm(ModelForm):
    problems = ModelMultipleChoiceField(
        label=_('Included problems'),
        queryset=Problem.objects.all(),
        required=False,
        help_text=_('These problems are included in this type of problems'),
        widget=HeavySelect2MultipleWidget(data_view='problem_select2'))


class ProblemTypeAdmin(admin.ModelAdmin):
    fields = ('name', 'full_name', 'problems')
    form = ProblemTypeForm

    def save_model(self, request, obj, form, change):
        super(ProblemTypeAdmin, self).save_model(request, obj, form, change)
        obj.problem_set = form.cleaned_data['problems']
        obj.save()

    def get_form(self, request, obj=None, **kwargs):
        self.form.base_fields['problems'].initial = [o.pk for o in obj.problem_set.all()] if obj else []
        return super(ProblemTypeAdmin, self).get_form(request, obj, **kwargs)


class NavigationBarAdmin(MPTTModelAdmin, SortableModelAdmin):
    list_display = ('label', 'key', 'path')
    fields = ('key', 'label', 'path', 'order', 'regex', 'parent')
    list_editable = ()  # Bug in SortableModelAdmin: 500 without list_editable being set
    mptt_level_indent = 20
    sortable = 'order'

    def __init__(self, *args, **kwargs):
        super(NavigationBarAdmin, self).__init__(*args, **kwargs)
        self.__save_model_calls = 0

    def save_model(self, request, obj, form, change):
        self.__save_model_calls += 1
        return super(NavigationBarAdmin, self).save_model(request, obj, form, change)

    def changelist_view(self, request, extra_context=None):
        self.__save_model_calls = 0
        with NavigationBar.objects.disable_mptt_updates():
            result = super(NavigationBarAdmin, self).changelist_view(request, extra_context)
        if self.__save_model_calls:
            with LockModel(write=(NavigationBar,)):
                NavigationBar.objects.rebuild()
        return result


class GenerateKeyTextInput(TextInput):
    def render(self, name, value, attrs=None):
        text = super(TextInput, self).render(name, value, attrs)
        return mark_safe(text + format_html(
            '''\
<a href="#" onclick="return false;" class="button" id="id_{0}_regen">Regenerate</a>
<script type="text/javascript">
(function ($) {{
    $(document).ready(function () {{
        $('#id_{0}_regen').click(function () {{
            var length = 100,
                charset = "abcdefghijklnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789`~!@#$%^&*()_+-=|[]{{}};:,<>./?",
                key = "";
            for (var i = 0, n = charset.length; i < length; ++i) {{
                key += charset.charAt(Math.floor(Math.random() * n));
            }}
            $('#id_{0}').val(key);
        }});
    }});
}})(django.jQuery);
</script>
''', name))


class JudgeAdminForm(ModelForm):
    class Meta:
        widgets = {
            'auth_key': GenerateKeyTextInput(),
        }


class JudgeAdmin(VersionAdmin):
    form = JudgeAdminForm
    readonly_fields = ('created', 'online', 'start_time', 'ping', 'load', 'last_ip', 'runtimes', 'problems')
    fieldsets = (
        (None, {'fields': ('name', 'auth_key')}),
        (_('Description'), {'fields': ('description',)}),
        (_('Information'), {'fields': ('created', 'online', 'last_ip', 'start_time', 'ping', 'load')}),
        (_('Capabilities'), {'fields': ('runtimes', 'problems')}),
    )
    list_display = ('name', 'online', 'start_time', 'ping', 'load', 'last_ip')
    ordering = ['-online', 'name']

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.online:
            return self.readonly_fields + ('name',)
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        result = super(JudgeAdmin, self).has_delete_permission(request, obj)
        if result and obj is not None:
            return not obj.online
        return result

    if AdminPagedownWidget is not None:
        formfield_overrides = {
            TextField: {'widget': AdminPagedownWidget},
        }


class ContestTagForm(ModelForm):
    contests = ModelMultipleChoiceField(
        label=_('Included contests'),
        queryset=Contest.objects.all(),
        required=False,
        widget=HeavySelect2MultipleWidget(data_view='contest_select2'))


class ContestTagAdmin(admin.ModelAdmin):
    fields = ('name', 'color', 'description', 'contests')
    list_display = ('name', 'color')
    actions_on_top = True
    actions_on_bottom = True
    form = ContestTagForm

    if AdminPagedownWidget is not None:
        formfield_overrides = {
            TextField: {'widget': AdminPagedownWidget},
        }

    def save_model(self, request, obj, form, change):
        super(ContestTagAdmin, self).save_model(request, obj, form, change)
        obj.contests = form.cleaned_data['contests']

    def get_form(self, request, obj=None, **kwargs):
        form = super(ContestTagAdmin, self).get_form(request, obj, **kwargs)
        if obj is not None:
            form.base_fields['contests'].initial = obj.contests.all()
        return form


class ContestProblemInlineForm(ModelForm):
    class Meta:
        widgets = {
            'problem': HeavySelect2Widget(data_view='problem_select2'),
        }


class ContestProblemInline(SortableTabularInline):
    model = ContestProblem
    verbose_name = _('Problem')
    verbose_name_plural = 'Problems'
    fields = ('problem', 'points', 'partial', 'output_prefix_override')
    form = ContestProblemInlineForm
    sortable = 'order'
    if SortableTabularInline is admin.TabularInline:
        fields += ('order',)


class ContestForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super(ContestForm, self).__init__(*args, **kwargs)
        if 'rate_exclude' in self.fields:
            self.fields['rate_exclude'].queryset = \
                Profile.objects.filter(contest_history__contest=self.instance).distinct()

    class Meta:
        widgets = {
            'organizers': HeavySelect2MultipleWidget(data_view='profile_select2'),
            'organizations': HeavySelect2MultipleWidget(data_view='organization_select2'),
            'tags': Select2MultipleWidget
        }

        if HeavyPreviewAdminPageDownWidget is not None:
            widgets['description'] = HeavyPreviewAdminPageDownWidget(preview=reverse_lazy('contest_preview'))


class ContestAdmin(VersionAdmin):
    fieldsets = (
        (None, {'fields': ('key', 'name', 'organizers', 'is_public', 'hide_problem_tags', 'run_pretests_only')}),
        (_('Scheduling'), {'fields': ('start_time', 'end_time', 'time_limit')}),
        (_('Details'), {'fields': ('description', 'og_image', 'tags', 'summary')}),
        (_('Rating'), {'fields': ('is_rated', 'rate_all', 'rate_exclude')}),
        (_('Organization'), {'fields': ('is_private', 'organizations')}),
    )
    list_display = ('key', 'name', 'is_public', 'is_rated', 'start_time', 'end_time', 'time_limit', 'user_count')
    actions = ['make_public', 'make_private']
    inlines = [ContestProblemInline]
    actions_on_top = True
    actions_on_bottom = True
    form = ContestForm
    change_list_template = 'admin/judge/contest/change_list.html'
    filter_horizontal = ['rate_exclude']

    def make_public(self, request, queryset):
        count = queryset.update(is_public=True)
        self.message_user(request, ungettext('%d contest successfully marked as public.',
                                             '%d contests successfully marked as public.',
                                             count) % count)

    make_public.short_description = _('Mark contests as public')

    def make_private(self, request, queryset):
        count = queryset.update(is_public=False)
        self.message_user(request, ungettext('%d contest successfully marked as private.',
                                             '%d contests successfully marked as private.',
                                             count) % count)

    make_private.short_description = _('Mark contests as private')

    def get_queryset(self, request):
        queryset = Contest.objects.all()
        if request.user.has_perm('judge.edit_all_contest'):
            return queryset
        else:
            return queryset.filter(organizers__id=request.user.profile.id)

    def get_readonly_fields(self, request, obj=None):
        if request.user.has_perm('judge.contest_rating'):
            return []
        return ['is_rated', 'rate_all', 'rate_exclude']

    def has_change_permission(self, request, obj=None):
        if not request.user.has_perm('judge.edit_own_contest'):
            return False
        if request.user.has_perm('judge.edit_all_contest') or obj is None:
            return True
        return obj.organizers.filter(id=request.user.profile.id).exists()

    def get_urls(self):
        return [
                   url(r'^rate/all/$', self.rate_all_view, name='judge_contest_rate_all'),
                   url(r'^(\d+)/rate/$', self.rate_view, name='judge_contest_rate')
               ] + super(ContestAdmin, self).get_urls()

    def rate_all_view(self, request):
        if not request.user.has_perm('judge.contest_rating'):
            raise PermissionDenied()
        with transaction.atomic():
            if connection.vendor == 'sqlite':
                Rating.objects.all().delete()
            else:
                cursor = connection.cursor()
                cursor.execute('TRUNCATE TABLE `%s`' % Rating._meta.db_table)
                cursor.close()
            Profile.objects.update(rating=None)
            for contest in Contest.objects.filter(is_rated=True).order_by('end_time'):
                rate_contest(contest)
        return HttpResponseRedirect(reverse('admin:judge_contest_changelist'))

    def rate_view(self, request, id):
        if not request.user.has_perm('judge.contest_rating'):
            raise PermissionDenied()
        contest = get_object_or_404(Contest, id=id)
        if not contest.is_rated:
            raise Http404()
        with transaction.atomic():
            Rating.objects.filter(contest__end_time__gte=contest.end_time).delete()
            for contest in Contest.objects.filter(is_rated=True, end_time__gte=contest.end_time).order_by('end_time'):
                rate_contest(contest)
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('admin:judge_contest_changelist')))

    def get_form(self, *args, **kwargs):
        form = super(ContestAdmin, self).get_form(*args, **kwargs)
        perms = ('edit_own_contest', 'edit_all_contest')
        form.base_fields['organizers'].queryset = Profile.objects.filter(
            Q(user__is_superuser=True) |
            Q(user__groups__permissions__codename__in=perms) |
            Q(user__user_permissions__codename__in=perms)
        ).distinct()
        return form


class ContestParticipationForm(ModelForm):
    class Meta:
        widgets = {
            'contest': Select2Widget(),
            'user': HeavySelect2Widget(data_view='profile_select2'),
        }


class ContestParticipationAdmin(admin.ModelAdmin):
    fields = ('contest', 'user', 'real_start', 'virtual')
    list_display = ('contest', 'username', 'show_virtual', 'real_start', 'score', 'cumtime')
    actions = ['recalculate_points', 'recalculate_cumtime']
    actions_on_bottom = actions_on_top = True
    search_fields = ('contest__key', 'contest__name', 'user__user__username', 'user__name')
    form = ContestParticipationForm

    def get_queryset(self, request):
        return super(ContestParticipationAdmin, self).get_queryset(request).only(
            'contest__name', 'user__user__username', 'user__name', 'real_start', 'score', 'cumtime', 'virtual'
        )

    def username(self, obj):
        return obj.user.long_display_name

    username.admin_order_field = 'user__user__username'

    def show_virtual(self, obj):
        return obj.virtual or '-'

    show_virtual.short_description = _('virtual')
    show_virtual.admin_order_field = 'virtual'

    def recalculate_points(self, request, queryset):
        count = 0
        for participation in queryset:
            participation.recalculate_score()
            count += 1
        self.message_user(request, ungettext('%d participation have scores recalculated.',
                                             '%d participations have scores recalculated.',
                                             count) % count)

    recalculate_points.short_description = _('Recalculate scores')

    def recalculate_cumtime(self, request, queryset):
        count = 0
        for participation in queryset:
            participation.update_cumtime()
            count += 1
        self.message_user(request, ungettext('%d participation have times recalculated.',
                                             '%d participations have times recalculated.',
                                             count) % count)

    recalculate_cumtime.short_description = _('Recalculate cumulative time')


class OrganizationForm(ModelForm):
    class Meta:
        widgets = {
            'admins': HeavySelect2MultipleWidget(data_view='profile_select2'),
            'registrant': HeavySelect2Widget(data_view='profile_select2'),
        }


class OrganizationAdmin(VersionAdmin):
    readonly_fields = ('creation_date',)
    fields = ('name', 'key', 'short_name', 'is_open', 'about', 'slots', 'registrant', 'creation_date', 'admins')
    list_display = ('name', 'key', 'short_name', 'is_open', 'slots', 'registrant', 'show_public')
    actions_on_top = True
    actions_on_bottom = True
    form = OrganizationForm

    def show_public(self, obj):
        format = '<a href="{0}" style="white-space:nowrap;">%s</a>' % ugettext('View on site')
        return format_html(format, obj.get_absolute_url())

    show_public.short_description = ''

    def get_readonly_fields(self, request, obj=None):
        fields = self.readonly_fields
        if not request.user.has_perm('judge.organization_admin'):
            return fields + ('registrant', 'admins', 'is_open', 'slots')
        return fields

    if MathJaxAdminPagedownWidget is not None:
        formfield_overrides = {
            TextField: {'widget': MathJaxAdminPagedownWidget},
        }

    def get_queryset(self, request):
        queryset = Organization.objects.all()
        if request.user.has_perm('judge.edit_all_organization'):
            return queryset
        else:
            return queryset.filter(admins=request.user.profile.id)

    def has_change_permission(self, request, obj=None):
        if not request.user.has_perm('judge.change_organization'):
            return False
        if request.user.has_perm('judge.edit_all_organization') or obj is None:
            return True
        return obj.admins.filter(id=request.user.profile.id).exists()


class BlogPostForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super(BlogPostForm, self).__init__(*args, **kwargs)
        self.fields['authors'].widget.can_add_related = False

    class Meta:
        widgets = {
            'authors': HeavySelect2MultipleWidget(data_view='profile_select2', attrs={'style': 'width: 100%'}),
        }

        if HeavyPreviewAdminPageDownWidget is not None:
            widgets['content'] = HeavyPreviewAdminPageDownWidget(preview=reverse_lazy('blog_preview'))
            widgets['summary'] = HeavyPreviewAdminPageDownWidget(preview=reverse_lazy('blog_preview'))


class BlogPostAdmin(VersionAdmin):
    fieldsets = (
        (None, {'fields': ('title', 'slug', 'authors', 'visible', 'sticky', 'publish_on')}),
        (_('Content'), {'fields': ('content', 'og_image',)}),
        (_('Summary'), {'classes': ('collapse',), 'fields': ('summary',)}),
    )
    prepopulated_fields = {'slug': ('title',)}
    list_display = ('id', 'title', 'visible', 'sticky', 'publish_on')
    list_display_links = ('id', 'title')
    ordering = ('-publish_on',)
    form = BlogPostForm

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser or (request.user.has_perm('judge.see_hidden_post') and
                (obj is None or obj.authors.filter(id=request.user.profile.id).exists()))


class SolutionForm(ModelForm):
    class Meta:
        widgets = {
            'problem': HeavySelect2Widget(data_view='problem_select2', attrs={'style': 'width: 250px'}),
        }


class SolutionAdmin(VersionAdmin):
    fields = ('url', 'title', 'is_public', 'publish_on', 'problem', 'content')
    list_display = ('title', 'url', 'problem_link', 'show_public')
    search_fields = ('url', 'title')
    form = SolutionForm

    def show_public(self, obj):
        format = '<a href="{0}" style="white-space:nowrap;">%s</a>' % ugettext('View on site')
        return format_html(format, obj.get_absolute_url())

    show_public.short_description = ''

    def problem_link(self, obj):
        if obj.problem is None:
            return 'N/A'
        return format_html(u'<a href="{}">{}</a>', reverse('admin:judge_problem_change', args=[obj.problem_id]),
                           obj.problem.name)

    problem_link.admin_order_field = 'problem__name'

    if MathJaxAdminPagedownWidget is not None:
        formfield_overrides = {
            TextField: {'widget': MathJaxAdminPagedownWidget},
        }


class LicenseAdmin(admin.ModelAdmin):
    fields = ('key', 'link', 'name', 'display', 'icon', 'text')
    list_display = ('name', 'key')

    if MathJaxAdminPagedownWidget is not None:
        formfield_overrides = {
            TextField: {'widget': MathJaxAdminPagedownWidget},
        }


class OrganizationRequestAdmin(admin.ModelAdmin):
    list_display = ('username', 'organization', 'state', 'time')
    readonly_fields = ('user', 'organization')

    def username(self, obj):
        return obj.user.long_display_name

    username.admin_order_field = 'user__user__username'


admin.site.register(Language, LanguageAdmin)
admin.site.register(Comment, CommentAdmin)
admin.site.register(Profile, ProfileAdmin)
admin.site.register(Problem, ProblemAdmin)
admin.site.register(ProblemGroup, ProblemGroupAdmin)
admin.site.register(ProblemType, ProblemTypeAdmin)
admin.site.register(Submission, SubmissionAdmin)
admin.site.register(MiscConfig)
admin.site.register(NavigationBar, NavigationBarAdmin)
admin.site.register(Judge, JudgeAdmin)
admin.site.register(Contest, ContestAdmin)
admin.site.register(ContestTag, ContestTagAdmin)
admin.site.register(ContestParticipation, ContestParticipationAdmin)
admin.site.register(Organization, OrganizationAdmin)
admin.site.register(BlogPost, BlogPostAdmin)
admin.site.register(Solution, SolutionAdmin)
admin.site.register(License, LicenseAdmin)
admin.site.register(OrganizationRequest, OrganizationRequestAdmin)
