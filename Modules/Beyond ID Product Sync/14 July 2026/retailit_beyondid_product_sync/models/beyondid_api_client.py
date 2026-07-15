import uuid

from odoo.addons.retailit_beyondid_manager.models.retailit_beyondid_api_client import (
    BeyondIdAuthorizationError,
    BeyondIdTransientError,
)
from odoo import _, models
from odoo.exceptions import UserError


class BeyondIdApiClient(models.AbstractModel):
    _inherit = "retailit.beyondid.api.client"

    def _post_multipart(self, path, fields, files, headers=None, config=None):
        config = config or self._get_config()
        boundary = "----OdooBeyondId%s" % uuid.uuid4().hex
        body = bytearray()

        def add_line(value):
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        for name, value in (fields or {}).items():
            add_line("--%s" % boundary)
            add_line('Content-Disposition: form-data; name="%s"' % name)
            add_line("")
            add_line(str(value if value is not None else ""))

        for name, file_data in (files or {}).items():
            filename, content, content_type = file_data
            if isinstance(content, str):
                content = content.encode("utf-8")
            add_line("--%s" % boundary)
            add_line(
                'Content-Disposition: form-data; name="%s"; filename="%s"'
                % (name, filename)
            )
            add_line("Content-Type: %s" % (content_type or "application/octet-stream"))
            add_line("")
            body.extend(content or b"")
            body.extend(b"\r\n")

        add_line("--%s--" % boundary)

        request_headers = {
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        }
        request_headers.update(headers or {})
        return self._post_raw(
            path,
            bytes(body),
            headers=request_headers,
            config=config,
            method="POST",
        )

    def _post_raw(self, path, data, headers=None, config=None, method="POST"):
        import socket
        import urllib.error
        import urllib.request

        config = config or self._get_config()
        request_headers = {
            "User-Agent": "Odoo Beyond ID Manager",
            "Accept": "application/json,text/plain,*/*",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self._build_url(path, config=config),
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:
                body = response.read().decode("utf-8", "replace")
                content_type = response.headers.get("content-type", "")
                return response.status, content_type, body
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            if error.code in (401, 403):
                raise BeyondIdAuthorizationError(
                    _("Beyond ID authorization failed with HTTP %(status)s: %(body)s")
                    % {
                        "status": error.code,
                        "body": body[:500] or error.reason,
                    }
                ) from error
            if error.code in (408, 429, 500, 502, 503, 504):
                raise BeyondIdTransientError(
                    _("Beyond ID returned temporary HTTP %(status)s: %(body)s")
                    % {
                        "status": error.code,
                        "body": body[:500] or error.reason,
                    }
                ) from error
            raise UserError(_("Beyond ID returned HTTP %(status)s: %(body)s") % {
                "status": error.code,
                "body": body[:500] or error.reason,
            }) from error
        except (TimeoutError, socket.timeout) as error:
            raise BeyondIdTransientError(
                _("Connection timed out while waiting for Beyond ID. The product batch was not confirmed by Odoo. Please try again later.")
            ) from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise BeyondIdTransientError(
                    _("Connection timed out while waiting for Beyond ID. The product batch was not confirmed by Odoo. Please try again later.")
                ) from error
            raise UserError(
                _("Connection failed. Odoo could not reach Beyond ID. Details: %s")
                % error.reason
            ) from error

    def upload_products_csv(self, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
        if operation not in ("import", "delete", "verify"):
            raise UserError(_("Unsupported Beyond ID product operation: %s") % operation)
        if authorized_context:
            config, headers = self._unpack_authorized_context(authorized_context)
        else:
            config, headers = self._get_authorized_context()
        filename = filename or "odoo_products_%s.csv" % operation
        try:
            _status, content_type, body = self._upload_products_csv_once(
                csv_content,
                operation,
                filename,
                config,
                headers,
                upload_options=upload_options,
            )
        except BeyondIdAuthorizationError:
            self._clear_token_cache()
            config, headers = self._get_authorized_context(config=config)
            self._update_authorized_context(authorized_context, config, headers)
            _status, content_type, body = self._upload_products_csv_once(
                csv_content,
                operation,
                filename,
                config,
                headers,
                upload_options=upload_options,
            )
        if "json" not in (content_type or "").lower() and not (body or "").strip().startswith("{"):
            raise UserError(_("Beyond ID did not return JSON for product %s.") % operation)
        return self._parse_json(body)

    def _unpack_authorized_context(self, authorized_context):
        if isinstance(authorized_context, dict):
            return authorized_context["config"], authorized_context["headers"]
        return authorized_context

    def _update_authorized_context(self, authorized_context, config, headers):
        if isinstance(authorized_context, dict):
            authorized_context["config"] = config
            authorized_context["headers"] = headers

    def _upload_products_csv_once(self, csv_content, operation, filename, config, headers, upload_options=None):
        fields = {
            "token": config["workspace_token"],
            "operation": operation,
        }
        fields.update(upload_options or {})
        return self._post_multipart(
            "/advancloud/import/upload",
            fields,
            {
                "file": (filename, csv_content, "text/csv; charset=utf-8"),
            },
            headers=headers,
            config=config,
        )
