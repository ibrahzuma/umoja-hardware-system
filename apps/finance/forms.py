from django import forms
from .models import Expense, BankAccount

class ExpenseForm(forms.ModelForm):
    bank = forms.ModelChoiceField(
        queryset=BankAccount.objects.filter(is_active=True),
        required=False,
        empty_label="— Select bank (optional) —",
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Expense
        fields = ['category', 'bank', 'description', 'amount', 'date_incurred', 'receipt_image']
        widgets = {
            'date_incurred': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'amount': forms.NumberInput(attrs={'class': 'form-control'}),
            'receipt_image': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*;capture=camera'}),
        }
