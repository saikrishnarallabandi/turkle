try:
    from cStringIO import StringIO
except ImportError:
    try:
        from StringIO import StringIO
    except ImportError:
        from io import BytesIO
        StringIO = BytesIO

from django.contrib import admin
from django.db import models
from django.forms import FileField, HiddenInput, ModelForm, TextInput, ValidationError
from django.urls import reverse
from django.utils.html import format_html, format_html_join
import unicodecsv

from hits.models import Hit, HitBatch, HitTemplate


admin.site.site_header = 'Turkle administration'


class HitBatchForm(ModelForm):
    csv_file = FileField(label='CSV File')

    def __init__(self, *args, **kwargs):
        super(HitBatchForm, self).__init__(*args, **kwargs)

        self.fields['allotted_assignment_time'].label = 'Allotted assignment time (hours)'
        self.fields['allotted_assignment_time'].help_text = 'If a user abandons a HIT, ' + \
            'this determines how long it takes until their assignment is deleted and ' + \
            'someone else can work on the HIT.'
        self.fields['hit_template'].label = 'HIT Template'
        self.fields['name'].label = 'Batch Name'

        # csv_file field not required if changing existing HitBatch
        #
        # When changing a HitBatch, the HitBatchAdmin.get_fields()
        # function will cause the form to be rendered without
        # displaying an HTML form field for the csv_file field.  I was
        # running into strange behavior where Django would still try
        # to validate the csv_file form field, even though there was
        # no way for the user to provide a value for this field.  The
        # two lines below prevent this problem from occurring, by
        # making the csv_file field optional when changing
        # a HitBatch.
        if not self.instance._state.adding:
            self.fields['csv_file'].required = False

    def clean(self):
        """Verify format of CSV file

        Verify that:
        - fieldnames in CSV file are identical to fieldnames in HIT template
        - number of fields in each row matches number of fields in CSV header
        """
        cleaned_data = super(HitBatchForm, self).clean()

        csv_file = cleaned_data.get("csv_file", False)
        hit_template = cleaned_data.get("hit_template", False)

        if not csv_file:
            return

        validation_errors = []

        rows = unicodecsv.reader(csv_file)
        header = next(rows)

        csv_fields = set(header)
        template_fields = set(hit_template.fieldnames)
        if csv_fields != template_fields:
            csv_but_not_template = csv_fields.difference(template_fields)
            if csv_but_not_template:
                validation_errors.append(
                    ValidationError(
                        'The CSV file contained fields that are not in the HIT template. '
                        'These extra fields are: %s' %
                        ', '.join(csv_but_not_template)))
            template_but_not_csv = template_fields.difference(csv_fields)
            if template_but_not_csv:
                validation_errors.append(
                    ValidationError(
                        'The CSV file is missing fields that are in the HIT template. '
                        'These missing fields are: %s' %
                        ', '.join(template_but_not_csv)))

        expected_fields = len(header)
        for (i, row) in enumerate(rows):
            if len(row) != expected_fields:
                validation_errors.append(
                    ValidationError(
                        'The CSV file header has %d fields, but line %d has %d fields' %
                        (expected_fields, i+2, len(row))))

        if validation_errors:
            raise ValidationError(validation_errors)

        # Rewind file, so it can be re-read
        csv_file.seek(0)


class HitBatchAdmin(admin.ModelAdmin):
    form = HitBatchForm
    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '60'})},
    }
    list_display = (
        'name', 'filename', 'total_hits', 'assignments_per_hit',
        'total_finished_hits', 'active', 'download_csv')

    def download_csv(self, obj):
        download_url = reverse('download_batch_csv', kwargs={'batch_id': obj.id})
        return format_html('<a href="{}">Download CSV results file</a>'.format(download_url))

    def get_fields(self, request, obj):
        # Display different fields when adding (when obj is None) vs changing a HitBatch
        if not obj:
            return ('hit_template', 'name', 'active', 'allotted_assignment_time',
                    'assignments_per_hit', 'csv_file')
        else:
            return ('hit_template', 'name', 'active', 'allotted_assignment_time',
                    'assignments_per_hit', 'filename')

    def get_readonly_fields(self, request, obj):
        if not obj:
            return []
        else:
            return ('assignments_per_hit', 'filename')

    def save_model(self, request, obj, form, change):
        if obj._state.adding:
            # Only use CSV file when adding HitBatch, not when changing
            obj.filename = request.FILES['csv_file']._name
            csv_text = request.FILES['csv_file'].read()
            super(HitBatchAdmin, self).save_model(request, obj, form, change)
            csv_fh = StringIO(csv_text)
            obj.create_hits_from_csv(csv_fh)
        else:
            super(HitBatchAdmin, self).save_model(request, obj, form, change)


class HitTemplateForm(ModelForm):
    template_file_upload = FileField(label='HTML template file', required=False)

    def __init__(self, *args, **kwargs):
        super(HitTemplateForm, self).__init__(*args, **kwargs)

        # This hidden form field is updated by JavaScript code in the
        # customized admin template file:
        #   hits/templates/admin/hits/hittemplate/change_form.html
        self.fields['filename'].widget = HiddenInput()

        self.fields['assignments_per_hit'].label = 'Assignments per HIT'
        self.fields['assignments_per_hit'].help_text = 'This parameter sets the default ' + \
            'number of Assignments per HIT for new Batches of HITs.  Changing this ' + \
            'parameter DOES NOT change the number of Assignments per HIT for already ' + \
            'published batches of HITS.'
        self.fields['form'].label = 'HTML template text'
        self.fields['form'].help_text = 'You can edit the template text directly, ' + \
            'or upload a template file using the button below'


class HitTemplateAdmin(admin.ModelAdmin):
    form = HitTemplateForm
    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '60'})},
    }
    list_display = ('name', 'filename', 'date_modified', 'active', 'publish_hits')

    # Fieldnames are extracted from form text, and should not be edited directly
    exclude = ('fieldnames',)
    readonly_fields = ('extracted_template_variables',)

    def extracted_template_variables(self, instance):
        return format_html_join('\n', "<li>{}</li>",
                                ((f, ) for f in instance.fieldnames.keys()))

    def get_fields(self, request, obj):
        if not obj:
            # Adding
            return ['name', 'assignments_per_hit', 'active', 'login_required',
                    'form', 'template_file_upload',
                    'filename']
        else:
            # Changing
            return ['name', 'assignments_per_hit', 'active', 'login_required',
                    'form', 'template_file_upload', 'extracted_template_variables',
                    'filename']

    def publish_hits(self, instance):
        publish_hits_url = '%s?hit_template=%d&assignments_per_hit=%d' % (
            reverse('admin:hits_hitbatch_add'),
            instance.id,
            instance.assignments_per_hit)
        return format_html('<a href="{}" class="button">Publish HITS</a>'.format(publish_hits_url))


admin.site.register(Hit)
admin.site.register(HitBatch, HitBatchAdmin)
admin.site.register(HitTemplate, HitTemplateAdmin)
