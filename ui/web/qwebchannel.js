// QWebChannel JavaScript Library
// From Qt framework - qtwebchannel/qwebchannel.js

/****************************************************************************
**
** Copyright (C) 2016 The Qt Company Ltd.
** Contact: https://www.qt.io/licensing/
**
** This file is part of the Qt WebChannel module.
**
** $QT_BEGIN_LICENSE:LGPL$
** Commercial License Usage
** Licensees holding valid commercial Qt licenses may use this file in
** accordance with the commercial license agreement provided with the
** Software or, alternatively, in accordance with the terms contained in
** a written agreement between you and The Qt Company. For licensing terms
** and conditions see https://www.qt.io/terms-conditions. For further
** information use the contact form at https://www.qt.io/contact-us.
**
** GNU Lesser General Public License Usage
** Alternatively, this file may be used under the terms of the GNU Lesser
** General Public License version 3 as published by the Free Software
** Foundation and appearing in the file LICENSE.LGPL3 included in the
** packaging of this file. Please review the following information to
** ensure the GNU Lesser General Public License version 3 requirements
** will be met: https://www.gnu.org/licenses/lgpl-3.0.html.
**
** $QT_END_LICENSE$
**
****************************************************************************/

var QWebChannelMessageTypes = {
    signal: 1,
    propertyUpdate: 2,
    init: 3,
    idle: 4,
    debug: 5,
    invokeMethod: 6,
    connectSignal: 7,
    disconnectSignal: 8,
    setProperty: 9,
    response: 10
};

var QWebChannel = function(transport, initCallback)
{
    if (typeof transport === "undefined") {
        var scriptPaths = document.getElementsByTagName("script");
        // this will only work if at least one script tag is present
        if (scriptPaths.length > 0) {
            // account for script path being directory
            var scriptPath = scriptPaths[scriptPaths.length - 1].src;
            var path = scriptPath.substring(0, scriptPath.lastIndexOf("/"));
            // explicitly require the qwebchannel.js file
            var basePath = path + "/";
            require(path + "/qwebchannel.js");
        }
    }

    this.transport = transport;
    this.initCallback = initCallback;
    this.objects = {};

    this._updatePendingRequestNum = 0;

    this._resetState();

    transport.messageReceived.connect(this._handleMessage.bind(this));
};

QWebChannel.prototype._resetState = function()
{
    this._responseStack = [];
    this._objectIdToObject = { "1": this };
    this._objectPathToId = { "": 1 };
};

QWebChannel.prototype._handleMessage = function(message)
{
    var messageJson = typeof message.data === "string" ? JSON.parse(message.data) : message.data;
    var type = messageJson.type;
    var data = messageJson.data;

    switch (type) {
        case QWebChannelMessageTypes.signal:
            this._handleSignal(data);
            break;
        case QWebChannelMessageTypes.response:
            this._handleResponse(data);
            break;
        case QWebChannelMessageTypes.propertyUpdate:
            this._handlePropertyUpdate(data);
            break;
        case QWebChannelMessageTypes.debug:
            console.debug.apply(console, data);
            break;
        case QWebChannelMessageTypes.invokeMethod:
            this._handleInvokeMethod(data);
            break;
        case QWebChannelMessageTypes.init:
            this._handleInit(data);
            break;
        case QWebChannelMessageTypes.idle:
            break;
        default:
            console.error("invalid message type:", type);
    }
};

QWebChannel.prototype._handleSignal = function(message)
{
    var object = this._objectIdToObject[message.object];
    var signalName = message.signal;
    var signalParams = message.params;

    if (!object) {
        console.error("cannot find object for signal:", message.object);
        return;
    }
    var signal = object[signalName];
    if (!signal) {
        console.error("cannot find signal '" + signalName + "' on object '" + object + "'");
        return;
    }

    // because signal connected to QObject we get params already in qt style
    // (QVariant, QList, QMap, ... as plain JavaScript values)
    signal.apply(object, signalParams);
};

QWebChannel.prototype._handlePropertyUpdate = function(message)
{
    for (var i = 0; i < message.length; ++i) {
        var objectId = message[i].object;
        var changedProperties = message[i].props;
        var object = this._objectIdToObject[objectId];

        if (!object) {
            console.error("cannot find object for property update:", objectId);
            return;
        }

        for (var propertyName in changedProperties) {
            var propertyValue = changedProperties[propertyName];
            object[propertyName] = propertyValue;
        }
    }
    if (this._updatePendingRequestNum > 0) {
        --this._updatePendingRequestNum;
    }
};

QWebChannel.prototype._handleInit = function(data)
{
    for (var i = 0; i < data.length; ++i) {
        var objectDescription = data[i];
        this._createObject(objectDescription);
    }

    // now send the pending responses, because they might trigger further init signals
    this._handleResponse();

    if (this.initCallback) {
        this.initCallback(this);
    }
};

QWebChannel.prototype._handleInvokeMethod = function(message)
{
    var requestId = message.id;
    var objectId = message.object;
    var method = message.method;
    var params = message.params;

    var object = this._objectIdToObject[objectId];
    if (!object) {
        console.error("cannot find object for method call:", objectId);
        return;
    }

    if (!object[method]) {
        console.error("cannot find method '" + method + "' on object '" + object + "'");
        return;
    }

    var responseCallback = (function() {
        var id = requestId;
        return function(returnValue) {
            this.transport.send({ type: QWebChannelMessageTypes.response, id: id, data: returnValue });
        };
    })();

    var args = [responseCallback].concat(params);
    object[method].apply(object, args);
};

QWebChannel.prototype._handleResponse = function(data)
{
    if (!data) {
        data = [];
    }
    for (var i = 0; i < data.length; ++i) {
        var request = this._responseStack.shift();
        if (!request) {
            console.error("Received response message without matching request", i, data.length);
            continue;
        }

        if (data[i] !== undefined) {
            request.callback.apply(null, [data[i]]);
        } else if (request.errorCallback) {
            request.errorCallback();
        }
    }
};

QWebChannel.prototype._createObject = function(objectDescription)
{
    var id = objectDescription[0];
    var qobject = objectDescription[1];
    var methods = objectDescription[2];
    var properties = objectDescription[3];
    var signals = objectDescription[4];
    var className = objectDescription[5];

    var qpyqtObject = new QObject(id, this);
    this._objectIdToObject[id] = qpyqtObject;
    this._objectPathToId[className] = id;

    // parse methods
    for (var methodName in methods) {
        var methodDesc = methods[methodName];
        var jsName = methodDesc[0];
        var slotName = methodDesc[1];
        var returnType = methodDesc[2];
        var params = methodDesc[3];

        qpyqtObject[jsName] = this._createMethod(qpyqtObject, slotName, params);
    }

    // parse properties
    for (var propertyName in properties) {
        var propertyDesc = properties[propertyName];
        var jsName = propertyDesc[0];
        var notifySignal = propertyDesc[1];
        var propertyType = propertyDesc[2];

        qpyqtObject[jsName] = undefined;
        qpyqtObject["__propertyChanged_" + jsName] = propertyDesc;

        // create the property getter and setter
        Object.defineProperty(qpyqtObject, jsName, {
            get: (function(name) {
                return function() {
                    return this["__property_" + name];
                };
            })(jsName),
            set: (function(name) {
                return function(value) {
                    this.__propertyChanged_signal[name].connect(this.__propertySetter[name]);
                    this["__property_" + name] = value;
                };
            })(jsName),
            configurable: true
        });
    }

    // parse signals
    for (var signalName in signals) {
        var signalDesc = signals[signalName];
        var signalParams = signalDesc[1];
        qpyqtObject[signalDesc[0]] = this._createSignal(qpyqtObject, signalParams);
    }

    return qpyqtObject;
};

QWebChannel.prototype._createMethod = function(qobject, name, params)
{
    var self = this;
    return function() {
        var args = Array.prototype.slice.call(arguments);
        var callback = args.length > 0 && args[0] instanceof Function ? args.shift() : (function() {});
        var errorCallback = args.length > 0 && args[0] instanceof Function ? args.shift() : (function() {});

        var methodId = self._objectPathToId[qobject.__id__] + "." + name;
        var request = { callback: callback, errorCallback: errorCallback, methodId: methodId };
        self._responseStack.push(request);

        self.transport.send({
            type: QWebChannelMessageTypes.invokeMethod,
            id: request.id,
            object: qobject.__id__,
            method: name,
            params: args
        });
    };
};

QWebChannel.prototype._createSignal = function(qobject, params)
{
    var signal = function() {
        var args = Array.prototype.slice.call(arguments);
        signal.signalEmitted.apply(signal, args);
    };

    signal.signalEmitted = function() {
        var args = Array.prototype.slice.call(arguments);
        this._qwebChannel.transport.send({
            type: QWebChannelMessageTypes.signal,
            object: qobject.__id__,
            signal: qobject.__id__ + "." + this._signalName,
            params: args
        });
    };

    return signal;
};

function QObject(id, webChannel)
{
    this.__id__ = id;
    this._qwebChannel = webChannel;
}

// default implementation for the signal wrapper that is added as dynamic property
QObject.prototype.__signalHelper = function() {};

// here we will store the signal wrappers
QObject.prototype.__propertyChanged_signal = {};
QObject.prototype.__propertySetter = {};

// and we will add a generic getter/setter for each property
QObject.prototype.__propertyChanged_helper = function(name, value) {
    if (value === undefined) {
        return this["__property_" + name];
    } else {
        this["__property_" + name] = value;
        var property = this["__propertyChanged_" + name];
        if (property && property[0] !== undefined) {
            var signal = this[property[0]];
            if (signal) {
                signal.signalEmitted(value);
            }
        }
    }
};

// Expose to global scope for use without module loader
window.QWebChannel = QWebChannel;
window.qtWebChannel = undefined; // will be set in the HTML when available