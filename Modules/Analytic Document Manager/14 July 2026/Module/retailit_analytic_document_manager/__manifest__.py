{
'name':'Retail IT Analytic Document Manager',
'version':'19.0.2.0',
'summary':'Multi-account analytic distribution automation for eCommerce, invoicing, bank reconciliation and Point of Sale',
'description':'''
Retail IT Analytic Document Manager
====================================

Developed and maintained by Retail IT (retailit.tech).

Automates analytic distribution across the full document chain - sale
orders, purchase orders, invoices, bank reconciliation, and Point of Sale
- using native, multi-account analytic distributions rather than a single
hardcoded account per document.

Features:
---------
* Database-driven, per-website analytic account mapping for eCommerce
  orders (no hardcoded client values)
* Multi-account analytic distribution at document header level
  (sale.order, purchase.order, account.move), applied in bulk to lines
* One-click bulk "Apply Analytic Distribution" action on the Bank
  Matching kanban screen, including already-reconciled transactions
* Retroactive sweep action to backfill missing analytic distributions
  on existing eCommerce orders and invoices
* Default analytic distribution per Point of Sale configuration, carried
  through to invoiced order lines and to session-closing journal entries,
  with per-line overrides available on the POS order
''',
'author':'Retail IT',
'website':'https://retailit.tech',
'maintainer':'Retail IT',
'category':'Retail IT/Accounting',
'license':'OPL-1',
'depends':['account','analytic','sale','purchase','website_sale','account_accountant','account_invoice_extract','point_of_sale'],
'data':[
'security/ir.model.access.csv',
'views/analytic_website_mapping_views.xml',
'views/account_move_views.xml',
'views/sale_order_views.xml',
'views/purchase_order_views.xml',
'views/analytic_distribution_wizard_views.xml',
'views/analytic_distribution_move_wizard_views.xml',
'views/pos_config_views.xml',
'views/res_config_settings_views.xml',
'views/pos_order_views.xml'
],
'assets':{
'web.assets_backend':[
'retailit_analytic_document_manager/static/src/js/bank_rec_control_panel_patch.js',
'retailit_analytic_document_manager/static/src/js/invoice_extract_form_patch.js',
],
},
'installable':True,
'application':False
}
