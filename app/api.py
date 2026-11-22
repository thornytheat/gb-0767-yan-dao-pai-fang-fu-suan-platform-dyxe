from flask import Blueprint, current_app, jsonify, request

from app.archive.certificate import issue
from app.archive.restore import restore_certificate
from app.archive.run import archive_run
from app.errors import InputError
from app.monitor.inspection import STAGES, Inspection
from app.monitor.source import sample
from app.rules import verification_for

api = Blueprint('api', __name__, url_prefix='/api')


def archive():
    return current_app.extensions['monitor_archive']


@api.get('/samples/<name>/<stage>')
def preview(name, stage):
    if stage not in STAGES:
        raise InputError('STAGE_UNKNOWN', '找不到监测工序', 404)
    return jsonify(getattr(Inspection(sample(name)), stage))


@api.post('/runs')
def create_run():
    packet, reused = archive_run(archive(), request.get_json(silent=True))
    return jsonify(run_id=packet['manifest']['run_id'], reused=reused, manifest=packet['manifest'],
                   verification=packet.get('verification'),
                   totals=packet['results']['hours']['totals'], deviations=packet['results']['reports']['deviations'])


@api.get('/runs')
def runs():
    return jsonify(runs=archive().history())


@api.get('/runs/<run_id>/<stage>')
def snapshot(run_id, stage):
    if stage not in STAGES:
        raise InputError('STAGE_UNKNOWN', '找不到监测工序', 404)
    return jsonify(archive().require(run_id)['results'][stage])


@api.get('/runs/<run_id>/verification')
def verification(run_id):
    return jsonify(verification_for(archive().require(run_id)))


@api.post('/restore')
def restore():
    payload = request.get_json(silent=True)
    if payload is None:
        raise InputError('BAD_CERTIFICATE', '请提交监测证据单 JSON')
    packet, reused, verification, totals = restore_certificate(archive(), payload)
    return jsonify(run_id=packet['manifest']['run_id'], restored=True, reused=reused,
                   verification=verification, totals=totals,
                   deviations=packet['results']['reports']['deviations']), 200 if reused else 201


@api.get('/export/<run_id>')
def export(run_id):
    response = jsonify(issue(archive().require(run_id)))
    response.headers['Content-Disposition'] = f'attachment; filename="monitor-{run_id}.json"'
    return response
