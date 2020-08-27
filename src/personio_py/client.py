import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type, Union
from urllib.parse import urljoin

import requests
from requests import Response

from personio_py import Absence, AbsenceType, Attendance, DynamicMapping, Employee
from personio_py.errors import MissingCredentialsError, PersonioApiError, PersonioError
from personio_py.models import PersonioResource

logger = logging.getLogger('personio_py')


class Personio:
    """
    the Personio API client.

    :param base_url: use this custom base URL instead of the default https://api.personio.de/v1/
    :param client_id: the client id for API authentication
           (if not provided, defaults to the ``CLIENT_ID`` environment variable)
    :param client_secret: the client secret for API authentication
           (if not provided, defaults to the ``CLIENT_SECRET`` environment variable)
    :param dynamic_fields: definition of expected dynamic fields.
           List of :py:class:`DynamicMapping` tuples.
    """

    BASE_URL = "https://api.personio.de/v1/"
    """base URL of the Personio HTTP API"""

    def __init__(self, base_url: str = None, client_id: str = None, client_secret: str = None,
                 dynamic_fields: List[DynamicMapping] = None):
        self.base_url = base_url or self.BASE_URL
        self.client_id = client_id or os.getenv('CLIENT_ID')
        self.client_secret = client_secret or os.getenv('CLIENT_SECRET')
        self.headers = {'accept': 'application/json'}
        self.authenticated = False
        self.dynamic_fields = dynamic_fields

    def authenticate(self):
        """
        Try to authenticate (using Personio's ``/auth`` endpoint) with the credentials
        (client ID and secret) that were provided to this instance.

        There should be no need to explicitly call this function, because you will be automatically
        authenticated when you make your first request.

        If the authentication was successful, the authentication token is stored in the
        headers dictionary (``self.headers``). This token will be sent with every request and
        it will be automatically rotated whenever necessary.
        If the authentication failed, a ``PersonioApiError`` will be raised.
        """
        if not (self.client_id and self.client_secret):
            raise MissingCredentialsError(
                "both client_id and client_secret must be provided in order to authenticate")
        url = urljoin(self.base_url, 'auth')
        logger.debug(f"authenticating to {url} with client_id {self.client_id}")
        params = {"client_id": self.client_id, "client_secret": self.client_secret}
        response = requests.request("POST", url, headers=self.headers, params=params)
        if response.ok:
            token = response.json()['data']['token']
            self.headers['Authorization'] = f"Bearer {token}"
            self.authenticated = True
        else:
            raise PersonioApiError.from_response(response)

    def request(self, path: str, method='GET', params: Dict[str, Any] = None,
                data: Dict[str, Any] = None, headers: Dict[str, str] = None,
                auth_rotation=True) -> Response:
        """
        Make a request against the Personio API.
        Returns the HTTP response, which might be successful or not.

        Rotation of authorization tokens is handled in this function. If no token is available
        (first request of this instance), the ``authenticate()`` function is called. On subsequent
        requests, the new authorization token is read from the response headers and replaces the
        previous one. Note that this behavior can be turned off by setting auth_rotation=False,
        which is required for some requests that do not return a new authorization token.

        :param path: the URL path for this request (relative to the Personio API base URL)
        :param method: the HTTP request method (default: GET)
        :param params: dictionary of URL parameters (optional)
        :param data: dictionary of data to send in the request body (optional)
        :param headers: additional request headers (authentication is handled separately)
        :param auth_rotation: set to True, if authentication keys should be rotated
               during this request (default: True)
        :return: the HTTP response (the caller is responsible for handling HTTP errors)
        """
        # check if we are already authenticated
        if not self.authenticated:
            self.authenticate()
        # define headers
        _headers = self.headers
        if headers:
            _headers.update(headers)
        # make the request
        url = urljoin(self.base_url, path)
        response = requests.request(method, url, headers=_headers, params=params, data=data)
        # re-new the authorization header
        authorization = response.headers.get('Authorization')
        if authorization:
            self.headers['Authorization'] = authorization
        elif auth_rotation:
            raise PersonioError("Missing Authorization Header in response")
        # return the response, let the caller handle any issues
        return response

    def request_json(self, path: str, method='GET', params: Dict[str, Any] = None,
                     data: Dict[str, Any] = None, auth_rotation=True) -> Dict[str, Any]:
        """
        Make a request against the Personio API, expecting a json response.
        Returns the parsed json response as dictionary. Will raise a PersonioApiError if the
        request fails.

        :param path: the URL path for this request (relative to the Personio API base URL)
        :param method: the HTTP request method (default: GET)
        :param params: dictionary of URL parameters (optional)
        :param data: dictionary of data to send in the request body (optional)
        :param auth_rotation: set to True, if authentication keys should be rotated
               during this request (default: True for json requests)
        :return: the parsed json response, when the request was successful, or a PersonioApiError
        """
        headers = {'accept': 'application/json'}
        response = self.request(path, method, params, data, headers, auth_rotation=auth_rotation)
        if response.ok:
            try:
                return response.json()
            except ValueError:
                raise PersonioError(f"Failed to parse response as json: {response.text}")
        else:
            raise PersonioApiError.from_response(response)

    def request_paginated(
            self, path: str, method='GET', params: Dict[str, Any] = None,
            data: Dict[str, Any] = None, auth_rotation=True, limit=200) -> Dict[str, Any]:
        """
        Make a request against the Personio API, expecting a json response that may be paginated,
        i.e. not all results might have been returned after the first request. Will continue
        to make requests until no more results are provided by the Personio API.
        Returns the parsed json response as dictionary. Will raise a PersonioApiError if the
        request fails.

        :param path: the URL path for this request (relative to the Personio API base URL)
        :param method: the HTTP request method (default: GET)
        :param params: dictionary of URL parameters (optional)
        :param data: dictionary of data to send in the request body (optional)
        :param auth_rotation: set to True, if authentication keys should be rotated
               during this request (default: True for json requests)
        :param limit: the max. number of items to return in response to a single request.
               A higher limit means fewer requests will be made (though there is an upper bound
               that is enforced on the server side)
        :return: the parsed json response, when the request was successful, or a PersonioApiError
        """
        # prepare the params dict (need limit and offset as parameters)
        if params is None:
            params = {}
        params['limit'] = limit
        params['offset'] = 0
        # continue making requests until no more data is returned
        data_acc = []
        while True:
            response = self.request_json(path, method, params, data, auth_rotation=auth_rotation)
            resp_data = response['data']
            if resp_data:
                data_acc.extend(resp_data)
                params['offset'] += len(resp_data)
            else:
                break
        # return the accumulated data
        response['data'] = data_acc
        return response

    def request_image(self, path: str, method='GET', params: Dict[str, Any] = None,
                      auth_rotation=False) -> Optional[bytes]:
        """
        Request an image file (as png or jpg) from the Personio API.
        Returns the image as byte array, or None, if no image is available for this resource
        (HTTP status 404).
        When any other errors occur, a PersonioApiError is raised.

        :param path: the URL path for this request (relative to the Personio API base URL)
        :param method: the HTTP request method (default: GET)
        :param params: dictionary of URL parameters (optional)
        :param auth_rotation: set to True, if authentication keys should be rotated
               during this request (default: False for image requests)
        :return: the image (bytes) or None, if no image is available
        """
        headers = {'accept': 'image/png, image/jpeg'}
        response = self.request(path, method, params, headers=headers, auth_rotation=auth_rotation)
        if response.ok:
            # great, we have our image as png or jpg
            return response.content
        elif response.status_code == 404:
            # no image? how disappointing...
            return None
        else:
            # oh noes, something went terribly wrong!
            raise PersonioApiError.from_response(response)

    def get_employees(self) -> List[Employee]:
        """
        Get a list of all employee records in your account.

        :return: list of ``Employee`` instances
        """
        response = self.request_json('company/employees')
        employees = [Employee.from_dict(d, self) for d in response['data']]
        return employees

    def get_employee(self, employee_id: int) -> Employee:
        """
        Get a single employee with the specified ID.

        :param employee_id: the Personio ID of the employee to fetch
        :return: an ``Employee`` instance or a PersonioApiError, if the employee does not exist
        """
        response = self.request_json(f'company/employees/{employee_id}')
        employee = Employee.from_dict(response['data'], self)
        return employee

    def get_employee_picture(self, employee_id: int, width: int = None) -> Optional[bytes]:
        """
        Get the profile picture of the employee with the specified ID as image file
        (usually png or jpg).

        :param employee_id: the Personio ID of the employee to fetch
        :param width: optionally scale the profile picture to this width.
               Defaults to the original width of the profile picture.
        :return: the profile picture as png or jpg file (bytes)
        """
        path = f'company/employees/{employee_id}/profile-picture'
        if width:
            path += f'/{width}'
        return self.request_image(path, auth_rotation=False)

    def create_employee(self, employee: Employee, refresh=True) -> Employee:
        """
        placeholder; not ready to be used
        """
        # TODO warn about limited selection of fields
        data = {
            'employee[email]': employee.email,
            'employee[first_name]': employee.first_name,
            'employee[last_name]': employee.last_name,
            'employee[gender]': employee.gender,
            'employee[position]': employee.position,
            'employee[department]': employee.department.name,
            'employee[hire_date]': employee.hire_date.isoformat()[:10],
            'employee[weekly_hours]': employee.weekly_working_hours,
        }
        response = self.request_json('company/employees', method='POST', data=data)
        employee.id_ = response['data']['id']
        if refresh:
            return self.get_employee(employee.id_)
        else:
            return employee

    def update_employee(self, employee: Employee):
        """
        placeholder; not ready to be used
        """
        # TODO implement
        pass

    def get_attendances(self, employee_ids: Union[int, List[int]], start_date: datetime = None,
                        end_date: datetime = None) -> List[Attendance]:
        """
        Get a list of all attendance records for the employees with the specified IDs

        Note that internally, multiple requests may be made by this function due to limitations
        of the Personio API: Only a limited number of records can be retrieved in a single request
        and only a limited number of employee IDs can be passed as URL parameters. The results
        are still presented as a single list, no matter how many requests are made.

        :param employee_ids: a single employee ID or a list of employee IDs.
               Attendance records for all matching employee IDs will be retrieved.
        :param start_date: only return attendance records from this date (inclusive, optional)
        :param end_date: only return attendance records up to this date (inclusive, optional)
        :return: list of ``Attendance`` records for the specified employees
        """
        return self._get_employee_metadata(
            'company/attendances', Attendance, employee_ids, start_date, end_date)

    def create_attendances(self, attendances: List[Attendance]):
        """
        placeholder; not ready to be used
        """
        # attendances can be created individually, but here you can push a huge bunch of items
        # in a single request, which can be significantly faster
        # TODO implement
        pass

    def update_attendance(self, attendance_id: int):
        """
        placeholder; not ready to be used
        """
        # TODO implement
        pass

    def delete_attendance(self, attendance_id: int):
        """
        placeholder; not ready to be used
        """
        # TODO implement
        pass

    def get_absence_types(self) -> List[AbsenceType]:
        """
        placeholder; not ready to be used
        """
        # TODO implement
        pass

    def get_absences(self, employee_ids: Union[int, List[int]], start_date: datetime = None,
                     end_date: datetime = None) -> List[Absence]:
        """
        Get a list of all absence records for the employees with the specified IDs.

        Note that internally, multiple requests may be made by this function due to limitations
        of the Personio API: Only a limited number of records can be retrieved in a single request
        and only a limited number of employee IDs can be passed as URL parameters. The results
        are still presented as a single list, no matter how many requests are made.

        :param employee_ids: a single employee ID or a list of employee IDs.
               Absence records for all matching employee IDs will be retrieved.
        :param start_date: only return absence records from this date (inclusive, optional)
        :param end_date: only return absence records up to this date (inclusive, optional)
        :return: list of ``Absence`` records for the specified employees
        """
        return self._get_employee_metadata(
            'company/time-offs', Absence, employee_ids, start_date, end_date)

    def get_absence(self, absence_id: int) -> Absence:
        """
        placeholder; not ready to be used
        """
        # TODO implement
        pass

    def create_absence(self, absence: Absence):
        """
        placeholder; not ready to be used
        """
        # TODO implement
        pass

    def delete_absence(self, absence_id: int):
        """
        placeholder; not ready to be used
        """
        # TODO implement
        pass

    def _get_employee_metadata(
            self, path: str, resource_cls: Type[PersonioResource],
            employee_ids: Union[int, List[int]], start_date: datetime = None,
            end_date: datetime = None):
        # resolve params to match API requirements
        employee_ids, start_date, end_date = self._normalize_timeframe_params(
            employee_ids, start_date, end_date)
        params = {
            "start_date": start_date.isoformat()[:10],
            "end_date": end_date.isoformat()[:10],
        }
        # request in batches of up to 50 employees (keeps URL length well below 2000 chars)
        data_acc = []
        for i in range(0, len(employee_ids), 50):
            params["employees[]"] = employee_ids[i:i + 50]
            response = self.request_paginated(path, params=params)
            data_acc.extend(response['data'])
        # create objects from accumulated API responses
        parsed_data = [resource_cls.from_dict(d, self) for d in data_acc]
        return parsed_data

    @classmethod
    def _normalize_timeframe_params(
            cls, employee_ids: Union[int, List[int]], start_date: datetime = None,
            end_date: datetime = None) -> Tuple[List[int], datetime, datetime]:
        """
        Whenever we need a list of employee IDs, a start date and an end date, this function comes
        in handy:

        * wraps a single employee ID into a list
        * sets the start date way into the past, if it was not provided
        * sets the end date way into the future, if it was not provided

        :param employee_ids: a single employee ID or a list of employee IDs
        :param start_date: a start date (optional)
        :param end_date: an end date (optional)
        :return: a tuple of (list of employee IDs, start date, end date), no None values.
        """
        if not employee_ids:
            raise ValueError("need at least one employee ID, got nothing")
        if start_date is None:
            start_date = datetime(1900, 1, 1)
        if end_date is None:
            end_date = datetime(datetime.now().year + 10, 1, 1)
        if not isinstance(employee_ids, list):
            employee_ids = [employee_ids]
        return employee_ids, start_date, end_date
