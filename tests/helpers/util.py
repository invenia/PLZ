from docker.errors import APIError, ImageNotFound


class MockAPIClient(object):
    def __init__(
        self,
        build_api_error=False,
        cc_image_error=False,
        cc_api_error=False,
        start_api_error=False,
        stop_api_error=False,
        rc_api_error=False,
        ec_api_error=False,
        es_api_error=False,
    ):
        self.build_api_error = build_api_error
        self.cc_image_error = cc_image_error
        self.cc_api_error = cc_api_error
        self.start_api_error = start_api_error
        self.stop_api_error = stop_api_error
        self.rc_api_error = rc_api_error
        self.ec_api_error = ec_api_error
        self.es_api_error = es_api_error

    def build(self, *args, **kwargs):
        if self.build_api_error:
            raise APIError("test api error")
        return [
            {"stream": "test stream"},
            {"stream": "test stream 2"},
            {"api": {"thing": "some other thing"}},
            {"stream": ""},
        ]

    def create_container(self, *args, **kwargs):
        if self.cc_image_error:
            raise ImageNotFound("test image not found")
        if self.cc_api_error:
            raise APIError("test api error")
        return {"Id": "test id"}

    def create_host_config(self, *args, **kwargs):
        pass

    def start(self, *args, **kwargs):
        if self.start_api_error:
            raise APIError("test api error")

    def stop(self, *args, **kwargs):
        if self.stop_api_error:
            raise APIError("test api error")

    def remove_container(self, *args, **kwargs):
        if self.rc_api_error:
            raise APIError("test api error")

    def exec_create(self, *args, **kwargs):
        if self.ec_api_error:
            raise APIError("test api error")
        return {"Id": "test id"}

    def exec_start(self, *args, **kwargs):
        if self.es_api_error:
            raise APIError("test api error")
        return [
            b"test stream ",
            b"test stream 2",
        ]
