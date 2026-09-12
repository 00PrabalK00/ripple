// Headless, read-only entry point into upstream RosScope services.
#include <QCoreApplication>
#include <QDateTime>
#include <QJsonDocument>
#include <QJsonArray>
#include <iostream>
#include "rrcc/process_manager.hpp"
#include "rrcc/ros_inspector.hpp"
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    const QString domain = qEnvironmentVariable("ROS_DOMAIN_ID", "0");
    const auto started = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
    rrcc::ProcessManager processes;
    rrcc::RosInspector inspector;
    const auto rows = processes.listProcesses(true, "", true);
    QJsonArray sanitized;
    for (const auto& value : rows) {
        const auto row = value.toObject();
        QJsonObject safe;
        // Do not export process environments or command lines (may contain keys).
        for (const auto* key : {"pid", "name", "state", "cpu_percent", "memory_percent", "executable", "ros_domain_id"})
            if (row.contains(key)) safe.insert(key, row.value(key));
        sanitized.append(safe);
    }
    QJsonObject result;
    result.insert("source", "RosScope");
    result.insert("schema_version", 1);
    result.insert("started_at", started);
    result.insert("domain", domain);
    result.insert("processes", sanitized);
    result.insert("tf_nav2", inspector.inspectTfNav2(domain));
    result.insert("completed_at", QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    std::cout << QJsonDocument(result).toJson(QJsonDocument::Compact).constData() << std::endl;
}
