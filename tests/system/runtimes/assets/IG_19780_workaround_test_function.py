def handler(context, event):
    return context.Response(
        body=type(event.body).__name__,
        headers={},
        content_type="text/plain",
        status_code=200,
    )
