var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __commonJS = (cb, mod) => function __require() {
  return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
};
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// ../frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/react@19.2.8/node_modules/react/cjs/react.production.js
var require_react_production = __commonJS({
  "../frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/react@19.2.8/node_modules/react/cjs/react.production.js"(exports2) {
    "use strict";
    var REACT_ELEMENT_TYPE = Symbol.for("react.transitional.element");
    var REACT_PORTAL_TYPE = Symbol.for("react.portal");
    var REACT_FRAGMENT_TYPE = Symbol.for("react.fragment");
    var REACT_STRICT_MODE_TYPE = Symbol.for("react.strict_mode");
    var REACT_PROFILER_TYPE = Symbol.for("react.profiler");
    var REACT_CONSUMER_TYPE = Symbol.for("react.consumer");
    var REACT_CONTEXT_TYPE = Symbol.for("react.context");
    var REACT_FORWARD_REF_TYPE = Symbol.for("react.forward_ref");
    var REACT_SUSPENSE_TYPE = Symbol.for("react.suspense");
    var REACT_MEMO_TYPE = Symbol.for("react.memo");
    var REACT_LAZY_TYPE = Symbol.for("react.lazy");
    var REACT_ACTIVITY_TYPE = Symbol.for("react.activity");
    var MAYBE_ITERATOR_SYMBOL = Symbol.iterator;
    function getIteratorFn(maybeIterable) {
      if (null === maybeIterable || "object" !== typeof maybeIterable) return null;
      maybeIterable = MAYBE_ITERATOR_SYMBOL && maybeIterable[MAYBE_ITERATOR_SYMBOL] || maybeIterable["@@iterator"];
      return "function" === typeof maybeIterable ? maybeIterable : null;
    }
    var ReactNoopUpdateQueue = {
      isMounted: function() {
        return false;
      },
      enqueueForceUpdate: function() {
      },
      enqueueReplaceState: function() {
      },
      enqueueSetState: function() {
      }
    };
    var assign = Object.assign;
    var emptyObject = {};
    function Component(props, context, updater) {
      this.props = props;
      this.context = context;
      this.refs = emptyObject;
      this.updater = updater || ReactNoopUpdateQueue;
    }
    Component.prototype.isReactComponent = {};
    Component.prototype.setState = function(partialState, callback) {
      if ("object" !== typeof partialState && "function" !== typeof partialState && null != partialState)
        throw Error(
          "takes an object of state variables to update or a function which returns an object of state variables."
        );
      this.updater.enqueueSetState(this, partialState, callback, "setState");
    };
    Component.prototype.forceUpdate = function(callback) {
      this.updater.enqueueForceUpdate(this, callback, "forceUpdate");
    };
    function ComponentDummy() {
    }
    ComponentDummy.prototype = Component.prototype;
    function PureComponent(props, context, updater) {
      this.props = props;
      this.context = context;
      this.refs = emptyObject;
      this.updater = updater || ReactNoopUpdateQueue;
    }
    var pureComponentPrototype = PureComponent.prototype = new ComponentDummy();
    pureComponentPrototype.constructor = PureComponent;
    assign(pureComponentPrototype, Component.prototype);
    pureComponentPrototype.isPureReactComponent = true;
    var isArrayImpl = Array.isArray;
    function noop() {
    }
    var ReactSharedInternals = { H: null, A: null, T: null, S: null };
    var hasOwnProperty = Object.prototype.hasOwnProperty;
    function ReactElement(type, key, props) {
      var refProp = props.ref;
      return {
        $$typeof: REACT_ELEMENT_TYPE,
        type,
        key,
        ref: void 0 !== refProp ? refProp : null,
        props
      };
    }
    function cloneAndReplaceKey(oldElement, newKey) {
      return ReactElement(oldElement.type, newKey, oldElement.props);
    }
    function isValidElement(object) {
      return "object" === typeof object && null !== object && object.$$typeof === REACT_ELEMENT_TYPE;
    }
    function escape(key) {
      var escaperLookup = { "=": "=0", ":": "=2" };
      return "$" + key.replace(/[=:]/g, function(match) {
        return escaperLookup[match];
      });
    }
    var userProvidedKeyEscapeRegex = /\/+/g;
    function getElementKey(element, index) {
      return "object" === typeof element && null !== element && null != element.key ? escape("" + element.key) : index.toString(36);
    }
    function resolveThenable(thenable) {
      switch (thenable.status) {
        case "fulfilled":
          return thenable.value;
        case "rejected":
          throw thenable.reason;
        default:
          switch ("string" === typeof thenable.status ? thenable.then(noop, noop) : (thenable.status = "pending", thenable.then(
            function(fulfilledValue) {
              "pending" === thenable.status && (thenable.status = "fulfilled", thenable.value = fulfilledValue);
            },
            function(error) {
              "pending" === thenable.status && (thenable.status = "rejected", thenable.reason = error);
            }
          )), thenable.status) {
            case "fulfilled":
              return thenable.value;
            case "rejected":
              throw thenable.reason;
          }
      }
      throw thenable;
    }
    function mapIntoArray(children, array, escapedPrefix, nameSoFar, callback) {
      var type = typeof children;
      if ("undefined" === type || "boolean" === type) children = null;
      var invokeCallback = false;
      if (null === children) invokeCallback = true;
      else
        switch (type) {
          case "bigint":
          case "string":
          case "number":
            invokeCallback = true;
            break;
          case "object":
            switch (children.$$typeof) {
              case REACT_ELEMENT_TYPE:
              case REACT_PORTAL_TYPE:
                invokeCallback = true;
                break;
              case REACT_LAZY_TYPE:
                return invokeCallback = children._init, mapIntoArray(
                  invokeCallback(children._payload),
                  array,
                  escapedPrefix,
                  nameSoFar,
                  callback
                );
            }
        }
      if (invokeCallback)
        return callback = callback(children), invokeCallback = "" === nameSoFar ? "." + getElementKey(children, 0) : nameSoFar, isArrayImpl(callback) ? (escapedPrefix = "", null != invokeCallback && (escapedPrefix = invokeCallback.replace(userProvidedKeyEscapeRegex, "$&/") + "/"), mapIntoArray(callback, array, escapedPrefix, "", function(c) {
          return c;
        })) : null != callback && (isValidElement(callback) && (callback = cloneAndReplaceKey(
          callback,
          escapedPrefix + (null == callback.key || children && children.key === callback.key ? "" : ("" + callback.key).replace(
            userProvidedKeyEscapeRegex,
            "$&/"
          ) + "/") + invokeCallback
        )), array.push(callback)), 1;
      invokeCallback = 0;
      var nextNamePrefix = "" === nameSoFar ? "." : nameSoFar + ":";
      if (isArrayImpl(children))
        for (var i = 0; i < children.length; i++)
          nameSoFar = children[i], type = nextNamePrefix + getElementKey(nameSoFar, i), invokeCallback += mapIntoArray(
            nameSoFar,
            array,
            escapedPrefix,
            type,
            callback
          );
      else if (i = getIteratorFn(children), "function" === typeof i)
        for (children = i.call(children), i = 0; !(nameSoFar = children.next()).done; )
          nameSoFar = nameSoFar.value, type = nextNamePrefix + getElementKey(nameSoFar, i++), invokeCallback += mapIntoArray(
            nameSoFar,
            array,
            escapedPrefix,
            type,
            callback
          );
      else if ("object" === type) {
        if ("function" === typeof children.then)
          return mapIntoArray(
            resolveThenable(children),
            array,
            escapedPrefix,
            nameSoFar,
            callback
          );
        array = String(children);
        throw Error(
          "Objects are not valid as a React child (found: " + ("[object Object]" === array ? "object with keys {" + Object.keys(children).join(", ") + "}" : array) + "). If you meant to render a collection of children, use an array instead."
        );
      }
      return invokeCallback;
    }
    function mapChildren(children, func, context) {
      if (null == children) return children;
      var result = [], count = 0;
      mapIntoArray(children, result, "", "", function(child) {
        return func.call(context, child, count++);
      });
      return result;
    }
    function lazyInitializer(payload) {
      if (-1 === payload._status) {
        var ctor = payload._result;
        ctor = ctor();
        ctor.then(
          function(moduleObject) {
            if (0 === payload._status || -1 === payload._status)
              payload._status = 1, payload._result = moduleObject;
          },
          function(error) {
            if (0 === payload._status || -1 === payload._status)
              payload._status = 2, payload._result = error;
          }
        );
        -1 === payload._status && (payload._status = 0, payload._result = ctor);
      }
      if (1 === payload._status) return payload._result.default;
      throw payload._result;
    }
    var reportGlobalError = "function" === typeof reportError ? reportError : function(error) {
      if ("object" === typeof window && "function" === typeof window.ErrorEvent) {
        var event = new window.ErrorEvent("error", {
          bubbles: true,
          cancelable: true,
          message: "object" === typeof error && null !== error && "string" === typeof error.message ? String(error.message) : String(error),
          error
        });
        if (!window.dispatchEvent(event)) return;
      } else if ("object" === typeof process && "function" === typeof process.emit) {
        process.emit("uncaughtException", error);
        return;
      }
      console.error(error);
    };
    var Children = {
      map: mapChildren,
      forEach: function(children, forEachFunc, forEachContext) {
        mapChildren(
          children,
          function() {
            forEachFunc.apply(this, arguments);
          },
          forEachContext
        );
      },
      count: function(children) {
        var n = 0;
        mapChildren(children, function() {
          n++;
        });
        return n;
      },
      toArray: function(children) {
        return mapChildren(children, function(child) {
          return child;
        }) || [];
      },
      only: function(children) {
        if (!isValidElement(children))
          throw Error(
            "React.Children.only expected to receive a single React element child."
          );
        return children;
      }
    };
    exports2.Activity = REACT_ACTIVITY_TYPE;
    exports2.Children = Children;
    exports2.Component = Component;
    exports2.Fragment = REACT_FRAGMENT_TYPE;
    exports2.Profiler = REACT_PROFILER_TYPE;
    exports2.PureComponent = PureComponent;
    exports2.StrictMode = REACT_STRICT_MODE_TYPE;
    exports2.Suspense = REACT_SUSPENSE_TYPE;
    exports2.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE = ReactSharedInternals;
    exports2.__COMPILER_RUNTIME = {
      __proto__: null,
      c: function(size) {
        return ReactSharedInternals.H.useMemoCache(size);
      }
    };
    exports2.cache = function(fn) {
      return function() {
        return fn.apply(null, arguments);
      };
    };
    exports2.cacheSignal = function() {
      return null;
    };
    exports2.cloneElement = function(element, config, children) {
      if (null === element || void 0 === element)
        throw Error(
          "The argument must be a React element, but you passed " + element + "."
        );
      var props = assign({}, element.props), key = element.key;
      if (null != config)
        for (propName in void 0 !== config.key && (key = "" + config.key), config)
          !hasOwnProperty.call(config, propName) || "key" === propName || "__self" === propName || "__source" === propName || "ref" === propName && void 0 === config.ref || (props[propName] = config[propName]);
      var propName = arguments.length - 2;
      if (1 === propName) props.children = children;
      else if (1 < propName) {
        for (var childArray = Array(propName), i = 0; i < propName; i++)
          childArray[i] = arguments[i + 2];
        props.children = childArray;
      }
      return ReactElement(element.type, key, props);
    };
    exports2.createContext = function(defaultValue) {
      defaultValue = {
        $$typeof: REACT_CONTEXT_TYPE,
        _currentValue: defaultValue,
        _currentValue2: defaultValue,
        _threadCount: 0,
        Provider: null,
        Consumer: null
      };
      defaultValue.Provider = defaultValue;
      defaultValue.Consumer = {
        $$typeof: REACT_CONSUMER_TYPE,
        _context: defaultValue
      };
      return defaultValue;
    };
    exports2.createElement = function(type, config, children) {
      var propName, props = {}, key = null;
      if (null != config)
        for (propName in void 0 !== config.key && (key = "" + config.key), config)
          hasOwnProperty.call(config, propName) && "key" !== propName && "__self" !== propName && "__source" !== propName && (props[propName] = config[propName]);
      var childrenLength = arguments.length - 2;
      if (1 === childrenLength) props.children = children;
      else if (1 < childrenLength) {
        for (var childArray = Array(childrenLength), i = 0; i < childrenLength; i++)
          childArray[i] = arguments[i + 2];
        props.children = childArray;
      }
      if (type && type.defaultProps)
        for (propName in childrenLength = type.defaultProps, childrenLength)
          void 0 === props[propName] && (props[propName] = childrenLength[propName]);
      return ReactElement(type, key, props);
    };
    exports2.createRef = function() {
      return { current: null };
    };
    exports2.forwardRef = function(render) {
      return { $$typeof: REACT_FORWARD_REF_TYPE, render };
    };
    exports2.isValidElement = isValidElement;
    exports2.lazy = function(ctor) {
      return {
        $$typeof: REACT_LAZY_TYPE,
        _payload: { _status: -1, _result: ctor },
        _init: lazyInitializer
      };
    };
    exports2.memo = function(type, compare) {
      return {
        $$typeof: REACT_MEMO_TYPE,
        type,
        compare: void 0 === compare ? null : compare
      };
    };
    exports2.startTransition = function(scope) {
      var prevTransition = ReactSharedInternals.T, currentTransition = {};
      ReactSharedInternals.T = currentTransition;
      try {
        var returnValue = scope(), onStartTransitionFinish = ReactSharedInternals.S;
        null !== onStartTransitionFinish && onStartTransitionFinish(currentTransition, returnValue);
        "object" === typeof returnValue && null !== returnValue && "function" === typeof returnValue.then && returnValue.then(noop, reportGlobalError);
      } catch (error) {
        reportGlobalError(error);
      } finally {
        null !== prevTransition && null !== currentTransition.types && (prevTransition.types = currentTransition.types), ReactSharedInternals.T = prevTransition;
      }
    };
    exports2.unstable_useCacheRefresh = function() {
      return ReactSharedInternals.H.useCacheRefresh();
    };
    exports2.use = function(usable) {
      return ReactSharedInternals.H.use(usable);
    };
    exports2.useActionState = function(action, initialState, permalink) {
      return ReactSharedInternals.H.useActionState(action, initialState, permalink);
    };
    exports2.useCallback = function(callback, deps) {
      return ReactSharedInternals.H.useCallback(callback, deps);
    };
    exports2.useContext = function(Context) {
      return ReactSharedInternals.H.useContext(Context);
    };
    exports2.useDebugValue = function() {
    };
    exports2.useDeferredValue = function(value, initialValue) {
      return ReactSharedInternals.H.useDeferredValue(value, initialValue);
    };
    exports2.useEffect = function(create2, deps) {
      return ReactSharedInternals.H.useEffect(create2, deps);
    };
    exports2.useEffectEvent = function(callback) {
      return ReactSharedInternals.H.useEffectEvent(callback);
    };
    exports2.useId = function() {
      return ReactSharedInternals.H.useId();
    };
    exports2.useImperativeHandle = function(ref, create2, deps) {
      return ReactSharedInternals.H.useImperativeHandle(ref, create2, deps);
    };
    exports2.useInsertionEffect = function(create2, deps) {
      return ReactSharedInternals.H.useInsertionEffect(create2, deps);
    };
    exports2.useLayoutEffect = function(create2, deps) {
      return ReactSharedInternals.H.useLayoutEffect(create2, deps);
    };
    exports2.useMemo = function(create2, deps) {
      return ReactSharedInternals.H.useMemo(create2, deps);
    };
    exports2.useOptimistic = function(passthrough, reducer) {
      return ReactSharedInternals.H.useOptimistic(passthrough, reducer);
    };
    exports2.useReducer = function(reducer, initialArg, init) {
      return ReactSharedInternals.H.useReducer(reducer, initialArg, init);
    };
    exports2.useRef = function(initialValue) {
      return ReactSharedInternals.H.useRef(initialValue);
    };
    exports2.useState = function(initialState) {
      return ReactSharedInternals.H.useState(initialState);
    };
    exports2.useSyncExternalStore = function(subscribe, getSnapshot, getServerSnapshot) {
      return ReactSharedInternals.H.useSyncExternalStore(
        subscribe,
        getSnapshot,
        getServerSnapshot
      );
    };
    exports2.useTransition = function() {
      return ReactSharedInternals.H.useTransition();
    };
    exports2.version = "19.2.8";
  }
});

// ../frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/react@19.2.8/node_modules/react/cjs/react.development.js
var require_react_development = __commonJS({
  "../frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/react@19.2.8/node_modules/react/cjs/react.development.js"(exports2, module2) {
    "use strict";
    "production" !== process.env.NODE_ENV && function() {
      function defineDeprecationWarning(methodName, info) {
        Object.defineProperty(Component.prototype, methodName, {
          get: function() {
            console.warn(
              "%s(...) is deprecated in plain JavaScript React classes. %s",
              info[0],
              info[1]
            );
          }
        });
      }
      function getIteratorFn(maybeIterable) {
        if (null === maybeIterable || "object" !== typeof maybeIterable)
          return null;
        maybeIterable = MAYBE_ITERATOR_SYMBOL && maybeIterable[MAYBE_ITERATOR_SYMBOL] || maybeIterable["@@iterator"];
        return "function" === typeof maybeIterable ? maybeIterable : null;
      }
      function warnNoop(publicInstance, callerName) {
        publicInstance = (publicInstance = publicInstance.constructor) && (publicInstance.displayName || publicInstance.name) || "ReactClass";
        var warningKey = publicInstance + "." + callerName;
        didWarnStateUpdateForUnmountedComponent[warningKey] || (console.error(
          "Can't call %s on a component that is not yet mounted. This is a no-op, but it might indicate a bug in your application. Instead, assign to `this.state` directly or define a `state = {};` class property with the desired state in the %s component.",
          callerName,
          publicInstance
        ), didWarnStateUpdateForUnmountedComponent[warningKey] = true);
      }
      function Component(props, context, updater) {
        this.props = props;
        this.context = context;
        this.refs = emptyObject;
        this.updater = updater || ReactNoopUpdateQueue;
      }
      function ComponentDummy() {
      }
      function PureComponent(props, context, updater) {
        this.props = props;
        this.context = context;
        this.refs = emptyObject;
        this.updater = updater || ReactNoopUpdateQueue;
      }
      function noop() {
      }
      function testStringCoercion(value) {
        return "" + value;
      }
      function checkKeyStringCoercion(value) {
        try {
          testStringCoercion(value);
          var JSCompiler_inline_result = false;
        } catch (e) {
          JSCompiler_inline_result = true;
        }
        if (JSCompiler_inline_result) {
          JSCompiler_inline_result = console;
          var JSCompiler_temp_const = JSCompiler_inline_result.error;
          var JSCompiler_inline_result$jscomp$0 = "function" === typeof Symbol && Symbol.toStringTag && value[Symbol.toStringTag] || value.constructor.name || "Object";
          JSCompiler_temp_const.call(
            JSCompiler_inline_result,
            "The provided key is an unsupported type %s. This value must be coerced to a string before using it here.",
            JSCompiler_inline_result$jscomp$0
          );
          return testStringCoercion(value);
        }
      }
      function getComponentNameFromType(type) {
        if (null == type) return null;
        if ("function" === typeof type)
          return type.$$typeof === REACT_CLIENT_REFERENCE ? null : type.displayName || type.name || null;
        if ("string" === typeof type) return type;
        switch (type) {
          case REACT_FRAGMENT_TYPE:
            return "Fragment";
          case REACT_PROFILER_TYPE:
            return "Profiler";
          case REACT_STRICT_MODE_TYPE:
            return "StrictMode";
          case REACT_SUSPENSE_TYPE:
            return "Suspense";
          case REACT_SUSPENSE_LIST_TYPE:
            return "SuspenseList";
          case REACT_ACTIVITY_TYPE:
            return "Activity";
        }
        if ("object" === typeof type)
          switch ("number" === typeof type.tag && console.error(
            "Received an unexpected object in getComponentNameFromType(). This is likely a bug in React. Please file an issue."
          ), type.$$typeof) {
            case REACT_PORTAL_TYPE:
              return "Portal";
            case REACT_CONTEXT_TYPE:
              return type.displayName || "Context";
            case REACT_CONSUMER_TYPE:
              return (type._context.displayName || "Context") + ".Consumer";
            case REACT_FORWARD_REF_TYPE:
              var innerType = type.render;
              type = type.displayName;
              type || (type = innerType.displayName || innerType.name || "", type = "" !== type ? "ForwardRef(" + type + ")" : "ForwardRef");
              return type;
            case REACT_MEMO_TYPE:
              return innerType = type.displayName || null, null !== innerType ? innerType : getComponentNameFromType(type.type) || "Memo";
            case REACT_LAZY_TYPE:
              innerType = type._payload;
              type = type._init;
              try {
                return getComponentNameFromType(type(innerType));
              } catch (x) {
              }
          }
        return null;
      }
      function getTaskName(type) {
        if (type === REACT_FRAGMENT_TYPE) return "<>";
        if ("object" === typeof type && null !== type && type.$$typeof === REACT_LAZY_TYPE)
          return "<...>";
        try {
          var name = getComponentNameFromType(type);
          return name ? "<" + name + ">" : "<...>";
        } catch (x) {
          return "<...>";
        }
      }
      function getOwner() {
        var dispatcher = ReactSharedInternals.A;
        return null === dispatcher ? null : dispatcher.getOwner();
      }
      function UnknownOwner() {
        return Error("react-stack-top-frame");
      }
      function hasValidKey(config) {
        if (hasOwnProperty.call(config, "key")) {
          var getter = Object.getOwnPropertyDescriptor(config, "key").get;
          if (getter && getter.isReactWarning) return false;
        }
        return void 0 !== config.key;
      }
      function defineKeyPropWarningGetter(props, displayName) {
        function warnAboutAccessingKey() {
          specialPropKeyWarningShown || (specialPropKeyWarningShown = true, console.error(
            "%s: `key` is not a prop. Trying to access it will result in `undefined` being returned. If you need to access the same value within the child component, you should pass it as a different prop. (https://react.dev/link/special-props)",
            displayName
          ));
        }
        warnAboutAccessingKey.isReactWarning = true;
        Object.defineProperty(props, "key", {
          get: warnAboutAccessingKey,
          configurable: true
        });
      }
      function elementRefGetterWithDeprecationWarning() {
        var componentName = getComponentNameFromType(this.type);
        didWarnAboutElementRef[componentName] || (didWarnAboutElementRef[componentName] = true, console.error(
          "Accessing element.ref was removed in React 19. ref is now a regular prop. It will be removed from the JSX Element type in a future release."
        ));
        componentName = this.props.ref;
        return void 0 !== componentName ? componentName : null;
      }
      function ReactElement(type, key, props, owner, debugStack, debugTask) {
        var refProp = props.ref;
        type = {
          $$typeof: REACT_ELEMENT_TYPE,
          type,
          key,
          props,
          _owner: owner
        };
        null !== (void 0 !== refProp ? refProp : null) ? Object.defineProperty(type, "ref", {
          enumerable: false,
          get: elementRefGetterWithDeprecationWarning
        }) : Object.defineProperty(type, "ref", { enumerable: false, value: null });
        type._store = {};
        Object.defineProperty(type._store, "validated", {
          configurable: false,
          enumerable: false,
          writable: true,
          value: 0
        });
        Object.defineProperty(type, "_debugInfo", {
          configurable: false,
          enumerable: false,
          writable: true,
          value: null
        });
        Object.defineProperty(type, "_debugStack", {
          configurable: false,
          enumerable: false,
          writable: true,
          value: debugStack
        });
        Object.defineProperty(type, "_debugTask", {
          configurable: false,
          enumerable: false,
          writable: true,
          value: debugTask
        });
        Object.freeze && (Object.freeze(type.props), Object.freeze(type));
        return type;
      }
      function cloneAndReplaceKey(oldElement, newKey) {
        newKey = ReactElement(
          oldElement.type,
          newKey,
          oldElement.props,
          oldElement._owner,
          oldElement._debugStack,
          oldElement._debugTask
        );
        oldElement._store && (newKey._store.validated = oldElement._store.validated);
        return newKey;
      }
      function validateChildKeys(node) {
        isValidElement(node) ? node._store && (node._store.validated = 1) : "object" === typeof node && null !== node && node.$$typeof === REACT_LAZY_TYPE && ("fulfilled" === node._payload.status ? isValidElement(node._payload.value) && node._payload.value._store && (node._payload.value._store.validated = 1) : node._store && (node._store.validated = 1));
      }
      function isValidElement(object) {
        return "object" === typeof object && null !== object && object.$$typeof === REACT_ELEMENT_TYPE;
      }
      function escape(key) {
        var escaperLookup = { "=": "=0", ":": "=2" };
        return "$" + key.replace(/[=:]/g, function(match) {
          return escaperLookup[match];
        });
      }
      function getElementKey(element, index) {
        return "object" === typeof element && null !== element && null != element.key ? (checkKeyStringCoercion(element.key), escape("" + element.key)) : index.toString(36);
      }
      function resolveThenable(thenable) {
        switch (thenable.status) {
          case "fulfilled":
            return thenable.value;
          case "rejected":
            throw thenable.reason;
          default:
            switch ("string" === typeof thenable.status ? thenable.then(noop, noop) : (thenable.status = "pending", thenable.then(
              function(fulfilledValue) {
                "pending" === thenable.status && (thenable.status = "fulfilled", thenable.value = fulfilledValue);
              },
              function(error) {
                "pending" === thenable.status && (thenable.status = "rejected", thenable.reason = error);
              }
            )), thenable.status) {
              case "fulfilled":
                return thenable.value;
              case "rejected":
                throw thenable.reason;
            }
        }
        throw thenable;
      }
      function mapIntoArray(children, array, escapedPrefix, nameSoFar, callback) {
        var type = typeof children;
        if ("undefined" === type || "boolean" === type) children = null;
        var invokeCallback = false;
        if (null === children) invokeCallback = true;
        else
          switch (type) {
            case "bigint":
            case "string":
            case "number":
              invokeCallback = true;
              break;
            case "object":
              switch (children.$$typeof) {
                case REACT_ELEMENT_TYPE:
                case REACT_PORTAL_TYPE:
                  invokeCallback = true;
                  break;
                case REACT_LAZY_TYPE:
                  return invokeCallback = children._init, mapIntoArray(
                    invokeCallback(children._payload),
                    array,
                    escapedPrefix,
                    nameSoFar,
                    callback
                  );
              }
          }
        if (invokeCallback) {
          invokeCallback = children;
          callback = callback(invokeCallback);
          var childKey = "" === nameSoFar ? "." + getElementKey(invokeCallback, 0) : nameSoFar;
          isArrayImpl(callback) ? (escapedPrefix = "", null != childKey && (escapedPrefix = childKey.replace(userProvidedKeyEscapeRegex, "$&/") + "/"), mapIntoArray(callback, array, escapedPrefix, "", function(c) {
            return c;
          })) : null != callback && (isValidElement(callback) && (null != callback.key && (invokeCallback && invokeCallback.key === callback.key || checkKeyStringCoercion(callback.key)), escapedPrefix = cloneAndReplaceKey(
            callback,
            escapedPrefix + (null == callback.key || invokeCallback && invokeCallback.key === callback.key ? "" : ("" + callback.key).replace(
              userProvidedKeyEscapeRegex,
              "$&/"
            ) + "/") + childKey
          ), "" !== nameSoFar && null != invokeCallback && isValidElement(invokeCallback) && null == invokeCallback.key && invokeCallback._store && !invokeCallback._store.validated && (escapedPrefix._store.validated = 2), callback = escapedPrefix), array.push(callback));
          return 1;
        }
        invokeCallback = 0;
        childKey = "" === nameSoFar ? "." : nameSoFar + ":";
        if (isArrayImpl(children))
          for (var i = 0; i < children.length; i++)
            nameSoFar = children[i], type = childKey + getElementKey(nameSoFar, i), invokeCallback += mapIntoArray(
              nameSoFar,
              array,
              escapedPrefix,
              type,
              callback
            );
        else if (i = getIteratorFn(children), "function" === typeof i)
          for (i === children.entries && (didWarnAboutMaps || console.warn(
            "Using Maps as children is not supported. Use an array of keyed ReactElements instead."
          ), didWarnAboutMaps = true), children = i.call(children), i = 0; !(nameSoFar = children.next()).done; )
            nameSoFar = nameSoFar.value, type = childKey + getElementKey(nameSoFar, i++), invokeCallback += mapIntoArray(
              nameSoFar,
              array,
              escapedPrefix,
              type,
              callback
            );
        else if ("object" === type) {
          if ("function" === typeof children.then)
            return mapIntoArray(
              resolveThenable(children),
              array,
              escapedPrefix,
              nameSoFar,
              callback
            );
          array = String(children);
          throw Error(
            "Objects are not valid as a React child (found: " + ("[object Object]" === array ? "object with keys {" + Object.keys(children).join(", ") + "}" : array) + "). If you meant to render a collection of children, use an array instead."
          );
        }
        return invokeCallback;
      }
      function mapChildren(children, func, context) {
        if (null == children) return children;
        var result = [], count = 0;
        mapIntoArray(children, result, "", "", function(child) {
          return func.call(context, child, count++);
        });
        return result;
      }
      function lazyInitializer(payload) {
        if (-1 === payload._status) {
          var ioInfo = payload._ioInfo;
          null != ioInfo && (ioInfo.start = ioInfo.end = performance.now());
          ioInfo = payload._result;
          var thenable = ioInfo();
          thenable.then(
            function(moduleObject) {
              if (0 === payload._status || -1 === payload._status) {
                payload._status = 1;
                payload._result = moduleObject;
                var _ioInfo = payload._ioInfo;
                null != _ioInfo && (_ioInfo.end = performance.now());
                void 0 === thenable.status && (thenable.status = "fulfilled", thenable.value = moduleObject);
              }
            },
            function(error) {
              if (0 === payload._status || -1 === payload._status) {
                payload._status = 2;
                payload._result = error;
                var _ioInfo2 = payload._ioInfo;
                null != _ioInfo2 && (_ioInfo2.end = performance.now());
                void 0 === thenable.status && (thenable.status = "rejected", thenable.reason = error);
              }
            }
          );
          ioInfo = payload._ioInfo;
          if (null != ioInfo) {
            ioInfo.value = thenable;
            var displayName = thenable.displayName;
            "string" === typeof displayName && (ioInfo.name = displayName);
          }
          -1 === payload._status && (payload._status = 0, payload._result = thenable);
        }
        if (1 === payload._status)
          return ioInfo = payload._result, void 0 === ioInfo && console.error(
            "lazy: Expected the result of a dynamic import() call. Instead received: %s\n\nYour code should look like: \n  const MyComponent = lazy(() => import('./MyComponent'))\n\nDid you accidentally put curly braces around the import?",
            ioInfo
          ), "default" in ioInfo || console.error(
            "lazy: Expected the result of a dynamic import() call. Instead received: %s\n\nYour code should look like: \n  const MyComponent = lazy(() => import('./MyComponent'))",
            ioInfo
          ), ioInfo.default;
        throw payload._result;
      }
      function resolveDispatcher() {
        var dispatcher = ReactSharedInternals.H;
        null === dispatcher && console.error(
          "Invalid hook call. Hooks can only be called inside of the body of a function component. This could happen for one of the following reasons:\n1. You might have mismatching versions of React and the renderer (such as React DOM)\n2. You might be breaking the Rules of Hooks\n3. You might have more than one copy of React in the same app\nSee https://react.dev/link/invalid-hook-call for tips about how to debug and fix this problem."
        );
        return dispatcher;
      }
      function releaseAsyncTransition() {
        ReactSharedInternals.asyncTransitions--;
      }
      function enqueueTask(task) {
        if (null === enqueueTaskImpl)
          try {
            var requireString = ("require" + Math.random()).slice(0, 7);
            enqueueTaskImpl = (module2 && module2[requireString]).call(
              module2,
              "timers"
            ).setImmediate;
          } catch (_err) {
            enqueueTaskImpl = function(callback) {
              false === didWarnAboutMessageChannel && (didWarnAboutMessageChannel = true, "undefined" === typeof MessageChannel && console.error(
                "This browser does not have a MessageChannel implementation, so enqueuing tasks via await act(async () => ...) will fail. Please file an issue at https://github.com/facebook/react/issues if you encounter this warning."
              ));
              var channel = new MessageChannel();
              channel.port1.onmessage = callback;
              channel.port2.postMessage(void 0);
            };
          }
        return enqueueTaskImpl(task);
      }
      function aggregateErrors(errors) {
        return 1 < errors.length && "function" === typeof AggregateError ? new AggregateError(errors) : errors[0];
      }
      function popActScope(prevActQueue, prevActScopeDepth) {
        prevActScopeDepth !== actScopeDepth - 1 && console.error(
          "You seem to have overlapping act() calls, this is not supported. Be sure to await previous act() calls before making a new one. "
        );
        actScopeDepth = prevActScopeDepth;
      }
      function recursivelyFlushAsyncActWork(returnValue, resolve, reject) {
        var queue = ReactSharedInternals.actQueue;
        if (null !== queue)
          if (0 !== queue.length)
            try {
              flushActQueue(queue);
              enqueueTask(function() {
                return recursivelyFlushAsyncActWork(returnValue, resolve, reject);
              });
              return;
            } catch (error) {
              ReactSharedInternals.thrownErrors.push(error);
            }
          else ReactSharedInternals.actQueue = null;
        0 < ReactSharedInternals.thrownErrors.length ? (queue = aggregateErrors(ReactSharedInternals.thrownErrors), ReactSharedInternals.thrownErrors.length = 0, reject(queue)) : resolve(returnValue);
      }
      function flushActQueue(queue) {
        if (!isFlushing) {
          isFlushing = true;
          var i = 0;
          try {
            for (; i < queue.length; i++) {
              var callback = queue[i];
              do {
                ReactSharedInternals.didUsePromise = false;
                var continuation = callback(false);
                if (null !== continuation) {
                  if (ReactSharedInternals.didUsePromise) {
                    queue[i] = callback;
                    queue.splice(0, i);
                    return;
                  }
                  callback = continuation;
                } else break;
              } while (1);
            }
            queue.length = 0;
          } catch (error) {
            queue.splice(0, i + 1), ReactSharedInternals.thrownErrors.push(error);
          } finally {
            isFlushing = false;
          }
        }
      }
      "undefined" !== typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ && "function" === typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStart && __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStart(Error());
      var REACT_ELEMENT_TYPE = Symbol.for("react.transitional.element"), REACT_PORTAL_TYPE = Symbol.for("react.portal"), REACT_FRAGMENT_TYPE = Symbol.for("react.fragment"), REACT_STRICT_MODE_TYPE = Symbol.for("react.strict_mode"), REACT_PROFILER_TYPE = Symbol.for("react.profiler"), REACT_CONSUMER_TYPE = Symbol.for("react.consumer"), REACT_CONTEXT_TYPE = Symbol.for("react.context"), REACT_FORWARD_REF_TYPE = Symbol.for("react.forward_ref"), REACT_SUSPENSE_TYPE = Symbol.for("react.suspense"), REACT_SUSPENSE_LIST_TYPE = Symbol.for("react.suspense_list"), REACT_MEMO_TYPE = Symbol.for("react.memo"), REACT_LAZY_TYPE = Symbol.for("react.lazy"), REACT_ACTIVITY_TYPE = Symbol.for("react.activity"), MAYBE_ITERATOR_SYMBOL = Symbol.iterator, didWarnStateUpdateForUnmountedComponent = {}, ReactNoopUpdateQueue = {
        isMounted: function() {
          return false;
        },
        enqueueForceUpdate: function(publicInstance) {
          warnNoop(publicInstance, "forceUpdate");
        },
        enqueueReplaceState: function(publicInstance) {
          warnNoop(publicInstance, "replaceState");
        },
        enqueueSetState: function(publicInstance) {
          warnNoop(publicInstance, "setState");
        }
      }, assign = Object.assign, emptyObject = {};
      Object.freeze(emptyObject);
      Component.prototype.isReactComponent = {};
      Component.prototype.setState = function(partialState, callback) {
        if ("object" !== typeof partialState && "function" !== typeof partialState && null != partialState)
          throw Error(
            "takes an object of state variables to update or a function which returns an object of state variables."
          );
        this.updater.enqueueSetState(this, partialState, callback, "setState");
      };
      Component.prototype.forceUpdate = function(callback) {
        this.updater.enqueueForceUpdate(this, callback, "forceUpdate");
      };
      var deprecatedAPIs = {
        isMounted: [
          "isMounted",
          "Instead, make sure to clean up subscriptions and pending requests in componentWillUnmount to prevent memory leaks."
        ],
        replaceState: [
          "replaceState",
          "Refactor your code to use setState instead (see https://github.com/facebook/react/issues/3236)."
        ]
      };
      for (fnName in deprecatedAPIs)
        deprecatedAPIs.hasOwnProperty(fnName) && defineDeprecationWarning(fnName, deprecatedAPIs[fnName]);
      ComponentDummy.prototype = Component.prototype;
      deprecatedAPIs = PureComponent.prototype = new ComponentDummy();
      deprecatedAPIs.constructor = PureComponent;
      assign(deprecatedAPIs, Component.prototype);
      deprecatedAPIs.isPureReactComponent = true;
      var isArrayImpl = Array.isArray, REACT_CLIENT_REFERENCE = Symbol.for("react.client.reference"), ReactSharedInternals = {
        H: null,
        A: null,
        T: null,
        S: null,
        actQueue: null,
        asyncTransitions: 0,
        isBatchingLegacy: false,
        didScheduleLegacyUpdate: false,
        didUsePromise: false,
        thrownErrors: [],
        getCurrentStack: null,
        recentlyCreatedOwnerStacks: 0
      }, hasOwnProperty = Object.prototype.hasOwnProperty, createTask = console.createTask ? console.createTask : function() {
        return null;
      };
      deprecatedAPIs = {
        react_stack_bottom_frame: function(callStackForError) {
          return callStackForError();
        }
      };
      var specialPropKeyWarningShown, didWarnAboutOldJSXRuntime;
      var didWarnAboutElementRef = {};
      var unknownOwnerDebugStack = deprecatedAPIs.react_stack_bottom_frame.bind(
        deprecatedAPIs,
        UnknownOwner
      )();
      var unknownOwnerDebugTask = createTask(getTaskName(UnknownOwner));
      var didWarnAboutMaps = false, userProvidedKeyEscapeRegex = /\/+/g, reportGlobalError = "function" === typeof reportError ? reportError : function(error) {
        if ("object" === typeof window && "function" === typeof window.ErrorEvent) {
          var event = new window.ErrorEvent("error", {
            bubbles: true,
            cancelable: true,
            message: "object" === typeof error && null !== error && "string" === typeof error.message ? String(error.message) : String(error),
            error
          });
          if (!window.dispatchEvent(event)) return;
        } else if ("object" === typeof process && "function" === typeof process.emit) {
          process.emit("uncaughtException", error);
          return;
        }
        console.error(error);
      }, didWarnAboutMessageChannel = false, enqueueTaskImpl = null, actScopeDepth = 0, didWarnNoAwaitAct = false, isFlushing = false, queueSeveralMicrotasks = "function" === typeof queueMicrotask ? function(callback) {
        queueMicrotask(function() {
          return queueMicrotask(callback);
        });
      } : enqueueTask;
      deprecatedAPIs = Object.freeze({
        __proto__: null,
        c: function(size) {
          return resolveDispatcher().useMemoCache(size);
        }
      });
      var fnName = {
        map: mapChildren,
        forEach: function(children, forEachFunc, forEachContext) {
          mapChildren(
            children,
            function() {
              forEachFunc.apply(this, arguments);
            },
            forEachContext
          );
        },
        count: function(children) {
          var n = 0;
          mapChildren(children, function() {
            n++;
          });
          return n;
        },
        toArray: function(children) {
          return mapChildren(children, function(child) {
            return child;
          }) || [];
        },
        only: function(children) {
          if (!isValidElement(children))
            throw Error(
              "React.Children.only expected to receive a single React element child."
            );
          return children;
        }
      };
      exports2.Activity = REACT_ACTIVITY_TYPE;
      exports2.Children = fnName;
      exports2.Component = Component;
      exports2.Fragment = REACT_FRAGMENT_TYPE;
      exports2.Profiler = REACT_PROFILER_TYPE;
      exports2.PureComponent = PureComponent;
      exports2.StrictMode = REACT_STRICT_MODE_TYPE;
      exports2.Suspense = REACT_SUSPENSE_TYPE;
      exports2.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE = ReactSharedInternals;
      exports2.__COMPILER_RUNTIME = deprecatedAPIs;
      exports2.act = function(callback) {
        var prevActQueue = ReactSharedInternals.actQueue, prevActScopeDepth = actScopeDepth;
        actScopeDepth++;
        var queue = ReactSharedInternals.actQueue = null !== prevActQueue ? prevActQueue : [], didAwaitActCall = false;
        try {
          var result = callback();
        } catch (error) {
          ReactSharedInternals.thrownErrors.push(error);
        }
        if (0 < ReactSharedInternals.thrownErrors.length)
          throw popActScope(prevActQueue, prevActScopeDepth), callback = aggregateErrors(ReactSharedInternals.thrownErrors), ReactSharedInternals.thrownErrors.length = 0, callback;
        if (null !== result && "object" === typeof result && "function" === typeof result.then) {
          var thenable = result;
          queueSeveralMicrotasks(function() {
            didAwaitActCall || didWarnNoAwaitAct || (didWarnNoAwaitAct = true, console.error(
              "You called act(async () => ...) without await. This could lead to unexpected testing behaviour, interleaving multiple act calls and mixing their scopes. You should - await act(async () => ...);"
            ));
          });
          return {
            then: function(resolve, reject) {
              didAwaitActCall = true;
              thenable.then(
                function(returnValue) {
                  popActScope(prevActQueue, prevActScopeDepth);
                  if (0 === prevActScopeDepth) {
                    try {
                      flushActQueue(queue), enqueueTask(function() {
                        return recursivelyFlushAsyncActWork(
                          returnValue,
                          resolve,
                          reject
                        );
                      });
                    } catch (error$0) {
                      ReactSharedInternals.thrownErrors.push(error$0);
                    }
                    if (0 < ReactSharedInternals.thrownErrors.length) {
                      var _thrownError = aggregateErrors(
                        ReactSharedInternals.thrownErrors
                      );
                      ReactSharedInternals.thrownErrors.length = 0;
                      reject(_thrownError);
                    }
                  } else resolve(returnValue);
                },
                function(error) {
                  popActScope(prevActQueue, prevActScopeDepth);
                  0 < ReactSharedInternals.thrownErrors.length ? (error = aggregateErrors(
                    ReactSharedInternals.thrownErrors
                  ), ReactSharedInternals.thrownErrors.length = 0, reject(error)) : reject(error);
                }
              );
            }
          };
        }
        var returnValue$jscomp$0 = result;
        popActScope(prevActQueue, prevActScopeDepth);
        0 === prevActScopeDepth && (flushActQueue(queue), 0 !== queue.length && queueSeveralMicrotasks(function() {
          didAwaitActCall || didWarnNoAwaitAct || (didWarnNoAwaitAct = true, console.error(
            "A component suspended inside an `act` scope, but the `act` call was not awaited. When testing React components that depend on asynchronous data, you must await the result:\n\nawait act(() => ...)"
          ));
        }), ReactSharedInternals.actQueue = null);
        if (0 < ReactSharedInternals.thrownErrors.length)
          throw callback = aggregateErrors(ReactSharedInternals.thrownErrors), ReactSharedInternals.thrownErrors.length = 0, callback;
        return {
          then: function(resolve, reject) {
            didAwaitActCall = true;
            0 === prevActScopeDepth ? (ReactSharedInternals.actQueue = queue, enqueueTask(function() {
              return recursivelyFlushAsyncActWork(
                returnValue$jscomp$0,
                resolve,
                reject
              );
            })) : resolve(returnValue$jscomp$0);
          }
        };
      };
      exports2.cache = function(fn) {
        return function() {
          return fn.apply(null, arguments);
        };
      };
      exports2.cacheSignal = function() {
        return null;
      };
      exports2.captureOwnerStack = function() {
        var getCurrentStack = ReactSharedInternals.getCurrentStack;
        return null === getCurrentStack ? null : getCurrentStack();
      };
      exports2.cloneElement = function(element, config, children) {
        if (null === element || void 0 === element)
          throw Error(
            "The argument must be a React element, but you passed " + element + "."
          );
        var props = assign({}, element.props), key = element.key, owner = element._owner;
        if (null != config) {
          var JSCompiler_inline_result;
          a: {
            if (hasOwnProperty.call(config, "ref") && (JSCompiler_inline_result = Object.getOwnPropertyDescriptor(
              config,
              "ref"
            ).get) && JSCompiler_inline_result.isReactWarning) {
              JSCompiler_inline_result = false;
              break a;
            }
            JSCompiler_inline_result = void 0 !== config.ref;
          }
          JSCompiler_inline_result && (owner = getOwner());
          hasValidKey(config) && (checkKeyStringCoercion(config.key), key = "" + config.key);
          for (propName in config)
            !hasOwnProperty.call(config, propName) || "key" === propName || "__self" === propName || "__source" === propName || "ref" === propName && void 0 === config.ref || (props[propName] = config[propName]);
        }
        var propName = arguments.length - 2;
        if (1 === propName) props.children = children;
        else if (1 < propName) {
          JSCompiler_inline_result = Array(propName);
          for (var i = 0; i < propName; i++)
            JSCompiler_inline_result[i] = arguments[i + 2];
          props.children = JSCompiler_inline_result;
        }
        props = ReactElement(
          element.type,
          key,
          props,
          owner,
          element._debugStack,
          element._debugTask
        );
        for (key = 2; key < arguments.length; key++)
          validateChildKeys(arguments[key]);
        return props;
      };
      exports2.createContext = function(defaultValue) {
        defaultValue = {
          $$typeof: REACT_CONTEXT_TYPE,
          _currentValue: defaultValue,
          _currentValue2: defaultValue,
          _threadCount: 0,
          Provider: null,
          Consumer: null
        };
        defaultValue.Provider = defaultValue;
        defaultValue.Consumer = {
          $$typeof: REACT_CONSUMER_TYPE,
          _context: defaultValue
        };
        defaultValue._currentRenderer = null;
        defaultValue._currentRenderer2 = null;
        return defaultValue;
      };
      exports2.createElement = function(type, config, children) {
        for (var i = 2; i < arguments.length; i++)
          validateChildKeys(arguments[i]);
        i = {};
        var key = null;
        if (null != config)
          for (propName in didWarnAboutOldJSXRuntime || !("__self" in config) || "key" in config || (didWarnAboutOldJSXRuntime = true, console.warn(
            "Your app (or one of its dependencies) is using an outdated JSX transform. Update to the modern JSX transform for faster performance: https://react.dev/link/new-jsx-transform"
          )), hasValidKey(config) && (checkKeyStringCoercion(config.key), key = "" + config.key), config)
            hasOwnProperty.call(config, propName) && "key" !== propName && "__self" !== propName && "__source" !== propName && (i[propName] = config[propName]);
        var childrenLength = arguments.length - 2;
        if (1 === childrenLength) i.children = children;
        else if (1 < childrenLength) {
          for (var childArray = Array(childrenLength), _i = 0; _i < childrenLength; _i++)
            childArray[_i] = arguments[_i + 2];
          Object.freeze && Object.freeze(childArray);
          i.children = childArray;
        }
        if (type && type.defaultProps)
          for (propName in childrenLength = type.defaultProps, childrenLength)
            void 0 === i[propName] && (i[propName] = childrenLength[propName]);
        key && defineKeyPropWarningGetter(
          i,
          "function" === typeof type ? type.displayName || type.name || "Unknown" : type
        );
        var propName = 1e4 > ReactSharedInternals.recentlyCreatedOwnerStacks++;
        return ReactElement(
          type,
          key,
          i,
          getOwner(),
          propName ? Error("react-stack-top-frame") : unknownOwnerDebugStack,
          propName ? createTask(getTaskName(type)) : unknownOwnerDebugTask
        );
      };
      exports2.createRef = function() {
        var refObject = { current: null };
        Object.seal(refObject);
        return refObject;
      };
      exports2.forwardRef = function(render) {
        null != render && render.$$typeof === REACT_MEMO_TYPE ? console.error(
          "forwardRef requires a render function but received a `memo` component. Instead of forwardRef(memo(...)), use memo(forwardRef(...))."
        ) : "function" !== typeof render ? console.error(
          "forwardRef requires a render function but was given %s.",
          null === render ? "null" : typeof render
        ) : 0 !== render.length && 2 !== render.length && console.error(
          "forwardRef render functions accept exactly two parameters: props and ref. %s",
          1 === render.length ? "Did you forget to use the ref parameter?" : "Any additional parameter will be undefined."
        );
        null != render && null != render.defaultProps && console.error(
          "forwardRef render functions do not support defaultProps. Did you accidentally pass a React component?"
        );
        var elementType = { $$typeof: REACT_FORWARD_REF_TYPE, render }, ownName;
        Object.defineProperty(elementType, "displayName", {
          enumerable: false,
          configurable: true,
          get: function() {
            return ownName;
          },
          set: function(name) {
            ownName = name;
            render.name || render.displayName || (Object.defineProperty(render, "name", { value: name }), render.displayName = name);
          }
        });
        return elementType;
      };
      exports2.isValidElement = isValidElement;
      exports2.lazy = function(ctor) {
        ctor = { _status: -1, _result: ctor };
        var lazyType = {
          $$typeof: REACT_LAZY_TYPE,
          _payload: ctor,
          _init: lazyInitializer
        }, ioInfo = {
          name: "lazy",
          start: -1,
          end: -1,
          value: null,
          owner: null,
          debugStack: Error("react-stack-top-frame"),
          debugTask: console.createTask ? console.createTask("lazy()") : null
        };
        ctor._ioInfo = ioInfo;
        lazyType._debugInfo = [{ awaited: ioInfo }];
        return lazyType;
      };
      exports2.memo = function(type, compare) {
        null == type && console.error(
          "memo: The first argument must be a component. Instead received: %s",
          null === type ? "null" : typeof type
        );
        compare = {
          $$typeof: REACT_MEMO_TYPE,
          type,
          compare: void 0 === compare ? null : compare
        };
        var ownName;
        Object.defineProperty(compare, "displayName", {
          enumerable: false,
          configurable: true,
          get: function() {
            return ownName;
          },
          set: function(name) {
            ownName = name;
            type.name || type.displayName || (Object.defineProperty(type, "name", { value: name }), type.displayName = name);
          }
        });
        return compare;
      };
      exports2.startTransition = function(scope) {
        var prevTransition = ReactSharedInternals.T, currentTransition = {};
        currentTransition._updatedFibers = /* @__PURE__ */ new Set();
        ReactSharedInternals.T = currentTransition;
        try {
          var returnValue = scope(), onStartTransitionFinish = ReactSharedInternals.S;
          null !== onStartTransitionFinish && onStartTransitionFinish(currentTransition, returnValue);
          "object" === typeof returnValue && null !== returnValue && "function" === typeof returnValue.then && (ReactSharedInternals.asyncTransitions++, returnValue.then(releaseAsyncTransition, releaseAsyncTransition), returnValue.then(noop, reportGlobalError));
        } catch (error) {
          reportGlobalError(error);
        } finally {
          null === prevTransition && currentTransition._updatedFibers && (scope = currentTransition._updatedFibers.size, currentTransition._updatedFibers.clear(), 10 < scope && console.warn(
            "Detected a large number of updates inside startTransition. If this is due to a subscription please re-write it to use React provided hooks. Otherwise concurrent mode guarantees are off the table."
          )), null !== prevTransition && null !== currentTransition.types && (null !== prevTransition.types && prevTransition.types !== currentTransition.types && console.error(
            "We expected inner Transitions to have transferred the outer types set and that you cannot add to the outer Transition while inside the inner.This is a bug in React."
          ), prevTransition.types = currentTransition.types), ReactSharedInternals.T = prevTransition;
        }
      };
      exports2.unstable_useCacheRefresh = function() {
        return resolveDispatcher().useCacheRefresh();
      };
      exports2.use = function(usable) {
        return resolveDispatcher().use(usable);
      };
      exports2.useActionState = function(action, initialState, permalink) {
        return resolveDispatcher().useActionState(
          action,
          initialState,
          permalink
        );
      };
      exports2.useCallback = function(callback, deps) {
        return resolveDispatcher().useCallback(callback, deps);
      };
      exports2.useContext = function(Context) {
        var dispatcher = resolveDispatcher();
        Context.$$typeof === REACT_CONSUMER_TYPE && console.error(
          "Calling useContext(Context.Consumer) is not supported and will cause bugs. Did you mean to call useContext(Context) instead?"
        );
        return dispatcher.useContext(Context);
      };
      exports2.useDebugValue = function(value, formatterFn) {
        return resolveDispatcher().useDebugValue(value, formatterFn);
      };
      exports2.useDeferredValue = function(value, initialValue) {
        return resolveDispatcher().useDeferredValue(value, initialValue);
      };
      exports2.useEffect = function(create2, deps) {
        null == create2 && console.warn(
          "React Hook useEffect requires an effect callback. Did you forget to pass a callback to the hook?"
        );
        return resolveDispatcher().useEffect(create2, deps);
      };
      exports2.useEffectEvent = function(callback) {
        return resolveDispatcher().useEffectEvent(callback);
      };
      exports2.useId = function() {
        return resolveDispatcher().useId();
      };
      exports2.useImperativeHandle = function(ref, create2, deps) {
        return resolveDispatcher().useImperativeHandle(ref, create2, deps);
      };
      exports2.useInsertionEffect = function(create2, deps) {
        null == create2 && console.warn(
          "React Hook useInsertionEffect requires an effect callback. Did you forget to pass a callback to the hook?"
        );
        return resolveDispatcher().useInsertionEffect(create2, deps);
      };
      exports2.useLayoutEffect = function(create2, deps) {
        null == create2 && console.warn(
          "React Hook useLayoutEffect requires an effect callback. Did you forget to pass a callback to the hook?"
        );
        return resolveDispatcher().useLayoutEffect(create2, deps);
      };
      exports2.useMemo = function(create2, deps) {
        return resolveDispatcher().useMemo(create2, deps);
      };
      exports2.useOptimistic = function(passthrough, reducer) {
        return resolveDispatcher().useOptimistic(passthrough, reducer);
      };
      exports2.useReducer = function(reducer, initialArg, init) {
        return resolveDispatcher().useReducer(reducer, initialArg, init);
      };
      exports2.useRef = function(initialValue) {
        return resolveDispatcher().useRef(initialValue);
      };
      exports2.useState = function(initialState) {
        return resolveDispatcher().useState(initialState);
      };
      exports2.useSyncExternalStore = function(subscribe, getSnapshot, getServerSnapshot) {
        return resolveDispatcher().useSyncExternalStore(
          subscribe,
          getSnapshot,
          getServerSnapshot
        );
      };
      exports2.useTransition = function() {
        return resolveDispatcher().useTransition();
      };
      exports2.version = "19.2.8";
      "undefined" !== typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ && "function" === typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStop && __REACT_DEVTOOLS_GLOBAL_HOOK__.registerInternalModuleStop(Error());
    }();
  }
});

// ../frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/react@19.2.8/node_modules/react/index.js
var require_react = __commonJS({
  "../frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/react@19.2.8/node_modules/react/index.js"(exports2, module2) {
    "use strict";
    if (process.env.NODE_ENV === "production") {
      module2.exports = require_react_production();
    } else {
      module2.exports = require_react_development();
    }
  }
});

// <stdin>
var stdin_exports = {};
__export(stdin_exports, {
  useSessionStore: () => useSessionStore
});
module.exports = __toCommonJS(stdin_exports);

// ../frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/zustand@5.0.14_@types+react@19.2.17_react@19.2.8/node_modules/zustand/esm/vanilla.mjs
var createStoreImpl = (createState) => {
  let state;
  const listeners = /* @__PURE__ */ new Set();
  const setState = (partial, replace) => {
    const nextState = typeof partial === "function" ? partial(state) : partial;
    if (!Object.is(nextState, state)) {
      const previousState = state;
      state = (replace != null ? replace : typeof nextState !== "object" || nextState === null) ? nextState : Object.assign({}, state, nextState);
      listeners.forEach((listener) => listener(state, previousState));
    }
  };
  const getState = () => state;
  const getInitialState = () => initialState;
  const subscribe = (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  };
  const api = { setState, getState, getInitialState, subscribe };
  const initialState = state = createState(setState, getState, api);
  return api;
};
var createStore = (createState) => createState ? createStoreImpl(createState) : createStoreImpl;

// ../frontend-reaudit-history-ds-20260921/packages/web/node_modules/.pnpm/zustand@5.0.14_@types+react@19.2.17_react@19.2.8/node_modules/zustand/esm/react.mjs
var import_react = __toESM(require_react(), 1);
var identity = (arg) => arg;
function useStore(api, selector = identity) {
  const slice = import_react.default.useSyncExternalStore(
    api.subscribe,
    import_react.default.useCallback(() => selector(api.getState()), [api, selector]),
    import_react.default.useCallback(() => selector(api.getInitialState()), [api, selector])
  );
  import_react.default.useDebugValue(slice);
  return slice;
}
var createImpl = (createState) => {
  const api = createStore(createState);
  const useBoundStore = (selector) => useStore(api, selector);
  Object.assign(useBoundStore, api);
  return useBoundStore;
};
var create = (createState) => createState ? createImpl(createState) : createImpl;

// stub:@/services/api
var fetchSessions = async () => ({});
var fetchSessionHistory = async () => ({});
var createSession = async () => ({});
var deleteSession = async () => ({});
var batchDeleteSessions = async () => ({});
var renameSession = async () => ({});
var branchSession = async () => ({});
var reimportSession = async () => ({});

// stub:@/demo/mockBackend
var isMockMode = () => false;

// stub:@/stores/uiStore
var useUIStore = { getState: () => ({}) };

// packages/web/src/utils/messageIdentity.ts
var messageIdentities = /* @__PURE__ */ new WeakMap();
var persistentIdentities = /* @__PURE__ */ new Map();
var nextLocalIdentity = 0;
function rememberMessageIdentity(message) {
  if (messageIdentities.has(message)) return;
  const persistentKey = message.blockId ? `block:${message.blockId}` : message.messageId ? `message:${message.messageId}` : null;
  if (persistentKey) {
    const existing = persistentIdentities.get(persistentKey);
    if (existing) {
      messageIdentities.set(message, existing);
      return;
    }
    const identity2 = `${persistentKey}:${nextLocalIdentity++}`;
    persistentIdentities.set(persistentKey, identity2);
    messageIdentities.set(message, identity2);
    return;
  }
  const nativeId = message.nativeItemId;
  const prefix = nativeId ? `native:${nativeId}` : "local";
  messageIdentities.set(message, `${prefix}:${nextLocalIdentity++}`);
}
function inheritMessageIdentity(next, previous) {
  messageIdentities.set(next, getMessageIdentity(previous));
}
function getMessageIdentity(message) {
  const existing = messageIdentities.get(message);
  if (existing) return existing;
  rememberMessageIdentity(message);
  return messageIdentities.get(message);
}

// packages/web/src/stores/messageOrdering.ts
var localMarkers = /* @__PURE__ */ new WeakSet();
function markLocalMarker(message) {
  localMarkers.add(message);
}
function isLocalMarker(message) {
  return localMarkers.has(message);
}
function canonicalHistory(display) {
  return display.filter((message) => !isLocalMarker(message));
}
var durableOffsets = /* @__PURE__ */ new WeakMap();
function markDurableRow(message, offset) {
  durableOffsets.set(message, offset);
}
function isDurableRow(message) {
  return durableOffsets.has(message);
}
function taskScopeKey(sessionId, meta, previous) {
  const worker = meta.workerId ?? previous?.workerId ?? "worker";
  const generation = meta.generation ?? previous?.generation ?? 0;
  const task = meta.taskSeq ?? previous?.taskSeq ?? meta.taskId ?? previous?.taskId ?? "task";
  return `${sessionId}:${worker}:${generation}:${task}`;
}
function liveProjectionKeys(message, scope) {
  const identity2 = [
    ...message.messageId ? [`message:${message.messageId}`] : [],
    ...message.blockId ? [`block:${message.blockId}`] : [],
    ...message.nativeItemId ? [`native:${message.nativeItemId}`] : []
  ];
  if (identity2.length > 0) return identity2.map((id) => `${message.role}:${id}`);
  return [`${message.role}:slot:${scope.taskKey}:${scope.slot}`];
}
function createWindow() {
  return { rows: /* @__PURE__ */ new Map(), start: null, end: null, total: 0, epoch: null, revision: 0 };
}
function windowFromSession(session) {
  const history = session.history ?? [];
  const start = session.historyStart ?? Math.max(0, (session.historyTotal ?? history.length) - history.length);
  const window2 = createWindow();
  history.forEach((message, index) => {
    const offset = start + index;
    markDurableRow(message, offset);
    window2.rows.set(offset, message);
  });
  window2.start = history.length > 0 ? start : null;
  window2.end = history.length > 0 ? start + history.length : null;
  window2.total = session.historyTotal ?? history.length;
  window2.epoch = session.historyEpoch ?? null;
  window2.revision = session.historyRevision ?? 0;
  return window2;
}
function mergeWindowPage(current, page) {
  const epoch = typeof page.historyEpoch === "string" && page.historyEpoch ? page.historyEpoch : null;
  const revision = typeof page.historyRevision === "number" ? page.historyRevision : 0;
  const rows = page.history ?? [];
  if (epoch && current.epoch && epoch !== current.epoch) {
    if (revision < current.revision) {
      return {
        window: current,
        accepted: false,
        replacedEpoch: false,
        newOffsets: 0,
        reason: "older-epoch-page"
      };
    }
    const next2 = createWindow();
    next2.epoch = epoch;
    next2.revision = revision;
    next2.total = page.total;
    rows.forEach((message, index) => {
      const offset = page.start + index;
      markDurableRow(message, offset);
      next2.rows.set(offset, message);
    });
    next2.start = rows.length > 0 ? page.start : null;
    next2.end = rows.length > 0 ? page.start + rows.length : null;
    return { window: next2, accepted: true, replacedEpoch: true, newOffsets: rows.length };
  }
  const next = {
    ...current,
    rows: new Map(current.rows)
  };
  if (epoch) next.epoch = epoch;
  next.revision = Math.max(current.revision, revision);
  next.total = Math.max(current.total, page.total);
  let changed = false;
  let newOffsets = 0;
  let blockedOverlap = false;
  rows.forEach((message, index) => {
    const offset = page.start + index;
    const existing = next.rows.get(offset);
    if (existing === void 0) {
      markDurableRow(message, offset);
      next.rows.set(offset, message);
      changed = true;
      newOffsets += 1;
      return;
    }
    if (revision < current.revision) {
      blockedOverlap = true;
      return;
    }
    if (existing !== message) {
      markDurableRow(message, offset);
      next.rows.set(offset, message);
      changed = true;
    }
  });
  if (changed || next.total !== current.total) {
    const offsets = [...next.rows.keys()];
    next.start = offsets.length > 0 ? Math.min(...offsets) : null;
    next.end = offsets.length > 0 ? Math.max(...offsets) + 1 : null;
    return { window: next, accepted: true, replacedEpoch: false, newOffsets };
  }
  return {
    window: current,
    accepted: false,
    replacedEpoch: false,
    newOffsets: 0,
    reason: blockedOverlap ? "older-revision-overlap" : "no-new-content"
  };
}
function windowRows(window2) {
  return [...window2.rows.entries()].sort((a, b) => a[0] - b[0]).map(([, message]) => message);
}

// packages/web/src/stores/sessionStore.ts
var EMPTY_UNREAD_SET = /* @__PURE__ */ new Set();
var wsTouchSeq = 0;
var localTouchSeq = 0;
var settingsTouchSeq = 0;
var localMessageSeq = 0;
var localMessageOrigins = /* @__PURE__ */ new WeakMap();
function queueIds(message) {
  return Array.isArray(message.queueItemIds) ? message.queueItemIds.filter((id) => typeof id === "string" && id.length > 0) : [];
}
function canonicalQueueId(id) {
  return id.startsWith("queue:") ? id.slice("queue:".length) : id;
}
function queueIdMatches(a, b) {
  return a === b || canonicalQueueId(a) === canonicalQueueId(b);
}
function queueSetMatches(ids, candidate) {
  return [...ids].some((id) => queueIdMatches(id, candidate));
}
function messageShapeKey(message) {
  return `${message.role}\0${message.content}\0${JSON.stringify(message.parts ?? null)}`;
}
function explicitMessageIdentity(message) {
  return [
    ...message.messageId ? [`message:${message.messageId}`] : [],
    ...message.blockId ? [`block:${message.blockId}`] : [],
    ...queueIds(message).map((id) => `queue:${canonicalQueueId(id)}`),
    ...message.nativeItemId ? [`native:${message.nativeItemId}`] : []
  ];
}
function hasExplicitIdentityOverlap(a, b) {
  const bIds = new Set(explicitMessageIdentity(b));
  return explicitMessageIdentity(a).some((id) => bIds.has(id));
}
function isLocallyOwnedUserMessage(message) {
  return message.role === "user" && (queueIds(message).length > 0 || message.nativeItemId?.startsWith("local:user:") === true);
}
function rememberLocalMessageOrigin(message, historyTotal) {
  if (isLocallyOwnedUserMessage(message) && !localMessageOrigins.has(message)) {
    localMessageOrigins.set(message, Math.max(0, historyTotal));
  }
}
function copyLocalMessageOrigin(next, previous) {
  const origin = localMessageOrigins.get(previous);
  if (origin !== void 0) localMessageOrigins.set(next, origin);
}
function withLocalUserIdentity(sessionId, message) {
  if (message.role !== "user") return message;
  if (queueIds(message).length > 0 || message.nativeItemId?.startsWith("local:user:")) {
    return message;
  }
  localMessageSeq += 1;
  return {
    ...message,
    nativeItemId: `local:user:${sessionId}:${localMessageSeq}`
  };
}
var SESSION_SETTING_KEYS = [
  "model",
  "permissionMode",
  "alwaysThinkingEnabled",
  "effort",
  "outputMode",
  "modelContextWindow",
  "modelAutoCompactTokenLimit"
];
var runtimeKeys = /* @__PURE__ */ new WeakMap();
function bindRuntimeKey(message, key) {
  runtimeKeys.set(message, key);
  return message;
}
function runtimeKeyOf(message) {
  return runtimeKeys.get(message) ?? null;
}
function historyPageSize() {
  return 50;
}
function sameWorkerGeneration(a, b) {
  return Boolean(a.workerId && b.workerId && a.workerId === b.workerId && (a.generation === void 0 || b.generation === void 0 || a.generation === b.generation));
}
function isOlderMeta(incoming, known) {
  if (incoming.serverEpoch && known.serverEpoch && incoming.serverEpoch !== known.serverEpoch) {
    return true;
  }
  if (incoming.generation !== void 0 && known.generation !== void 0) {
    if (incoming.generation < known.generation) return true;
    if (incoming.generation > known.generation) return false;
  }
  if (incoming.workerId && known.workerId && incoming.workerId !== known.workerId) {
    return incoming.generation === void 0 || known.generation === void 0 || incoming.generation <= known.generation;
  }
  if (incoming.taskSeq !== void 0 && known.taskSeq !== void 0) {
    if (incoming.taskSeq < known.taskSeq) return true;
    if (incoming.taskSeq > known.taskSeq) return false;
    if (incoming.taskId && known.taskId && incoming.taskId !== known.taskId) {
      return true;
    }
  }
  if (incoming.taskSeq === void 0 && known.taskSeq !== void 0 && sameWorkerGeneration(incoming, known)) {
    return true;
  }
  if (incoming.taskId && known.taskId && incoming.taskId !== known.taskId && incoming.taskSeq === void 0 && known.taskSeq === void 0 && sameWorkerGeneration(incoming, known)) {
    return true;
  }
  const sameStreamTarget = Boolean(
    incoming.itemId && known.itemId && incoming.itemId === known.itemId || incoming.turnId && known.turnId && incoming.turnId === known.turnId || !incoming.itemId && !known.itemId && !incoming.turnId && !known.turnId && sameWorkerGeneration(incoming, known) && incoming.taskSeq === known.taskSeq
  );
  if (sameStreamTarget && incoming.streamText !== void 0 && known.streamText !== void 0 && known.streamText.startsWith(incoming.streamText) && incoming.streamText.length <= known.streamText.length) {
    return true;
  }
  return false;
}
function isBlockedByTerminal(incoming, terminal, status) {
  if (!terminal) return false;
  if (isOlderMeta(incoming, terminal)) return true;
  if (status === "idle" && sameWorkerGeneration(incoming, terminal)) return false;
  if (status === "running" && sameWorkerGeneration(incoming, terminal) && incoming.taskSeq === void 0) {
    return true;
  }
  if (status === "stream" && sameWorkerGeneration(incoming, terminal) && terminal.taskSeq !== void 0 && incoming.taskSeq === void 0) {
    return true;
  }
  if (incoming.taskSeq !== void 0 && terminal.taskSeq !== void 0 && incoming.taskSeq <= terminal.taskSeq && (incoming.generation === void 0 || terminal.generation === void 0 || incoming.generation === terminal.generation)) {
    return true;
  }
  return false;
}
function valueEqual(a, b) {
  return Array.isArray(a) || Array.isArray(b) ? JSON.stringify(a ?? null) === JSON.stringify(b ?? null) : a === b;
}
function pickSessionSettings(session) {
  const result = {};
  for (const key of SESSION_SETTING_KEYS) {
    if (Object.prototype.hasOwnProperty.call(session, key)) {
      result[key] = session[key];
    }
  }
  return result;
}
function mergeSessionSettingPatch(session, patch) {
  return patch && Object.keys(patch).length > 0 ? { ...session, ...patch } : session;
}
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error || "Failed to save settings");
}
function sameSessionSnapshot(previous, next, server) {
  return Object.keys(server).every(
    (key) => valueEqual(previous[key], next[key])
  );
}
var SUMMARY_PROJECTION_FIELDS = [
  "summaryRevision",
  "lastUserPreview",
  "lastAssistantPreview",
  "lastDisplayPreview",
  "lastMessage",
  "historyTotal",
  "updatedAt",
  "workerStatus",
  "workerId",
  "workerGeneration",
  "workerTaskId",
  "workerTaskSeq"
];
function preserveNewerSummary(current, incoming) {
  const currentRevision = current.summaryRevision;
  const incomingRevision = incoming.summaryRevision;
  if (typeof currentRevision !== "number" || currentRevision <= (typeof incomingRevision === "number" ? incomingRevision : -1)) {
    return incoming;
  }
  const preserved = { ...incoming, summaryRevision: currentRevision };
  for (const field of SUMMARY_PROJECTION_FIELDS) {
    if (field in current) Object.assign(preserved, { [field]: current[field] });
  }
  return preserved;
}
function ensureTranscript(transcripts, session) {
  const existing = transcripts?.[session.id];
  if (existing && !transcriptIsStale(existing, session)) return existing;
  return {
    window: windowFromSession(session),
    runtime: [],
    anchorOffset: session.historyTotal ?? (session.history?.length ?? 0),
    serverEpoch: null
  };
}
function transcriptIsStale(transcript, session) {
  if (session.historyEpoch && transcript.window.epoch && session.historyEpoch !== transcript.window.epoch) return true;
  if (typeof session.historyTotal === "number" && session.historyTotal < transcript.window.total) return true;
  return false;
}
function runtimeRowCompatible(runtimeRow, durableRow) {
  if (runtimeRow.role !== durableRow.role) return false;
  if (runtimeRow.content === durableRow.content) return true;
  return runtimeRow.role !== "user";
}
function projectTranscript(transcript) {
  const { window: window2, runtime, anchorOffset } = transcript;
  const ordered = [...window2.rows.entries()].sort((a, b) => a[0] - b[0]);
  const display = [];
  for (const [offset, message] of ordered) {
    if (offset < anchorOffset) display.push(message);
  }
  const emitted = /* @__PURE__ */ new Set();
  let next = anchorOffset;
  let aligned = true;
  for (const row of runtime) {
    if (isLocalMarker(row)) {
      display.push(row);
      continue;
    }
    if (aligned) {
      let matched = -1;
      for (; ; ) {
        const durable = window2.rows.get(next);
        if (!durable) break;
        if (runtimeRowCompatible(row, durable)) {
          matched = next;
          break;
        }
        display.push(durable);
        emitted.add(next);
        next += 1;
      }
      if (matched >= 0) {
        display.push(window2.rows.get(matched));
        emitted.add(matched);
        next = matched + 1;
        continue;
      }
      aligned = false;
    }
    display.push(row);
  }
  for (const [offset, message] of ordered) {
    if (offset >= anchorOffset && !emitted.has(offset)) display.push(message);
  }
  return display;
}
function withTranscript(state, sessionId, transcript) {
  return {
    sessionTranscripts: { ...state.sessionTranscripts, [sessionId]: transcript }
  };
}
function mirrorHistory(sessions, sessionId, display) {
  const canonical = canonicalHistory(display);
  return sessions.map((session) => session.id === sessionId ? { ...session, history: canonical } : session);
}
function sessionOf(state, sessionId) {
  return state.sessions.find((session) => session.id === sessionId);
}
function explicitIdentityOf(message) {
  return [
    ...message.messageId ? [`message:${message.messageId}`] : [],
    ...message.blockId ? [`block:${message.blockId}`] : [],
    ...message.nativeItemId ? [`native:${message.nativeItemId}`] : []
  ];
}
function projectLiveRows(current, previousBuffer, taskKey, rows) {
  const display = current.slice();
  const indexes = {};
  const refs = {};
  let appended = 0;
  for (const [slot, live] of rows.entries()) {
    const keys = liveProjectionKeys(live, { taskKey, slot });
    const previousRow = previousBuffer?.messages[slot];
    const previousKeys = previousRow ? liveProjectionKeys(previousRow, {
      taskKey: previousBuffer?.taskKey ?? taskKey,
      slot
    }) : [];
    let targetIndex = -1;
    const sameTask = previousBuffer?.taskKey === taskKey;
    for (const key of sameTask ? [...keys, ...previousKeys] : keys) {
      const cached = previousBuffer?.projectionIndexes?.[key];
      if (cached === void 0 || cached < 0 || cached >= display.length) continue;
      const row = display[cached];
      if (row.role !== live.role) continue;
      if (previousBuffer?.projectionRefs?.[key] !== row) continue;
      targetIndex = cached;
      break;
    }
    if (targetIndex < 0 && previousRow && sameTask) {
      const found = display.indexOf(previousRow);
      if (found >= 0 && display[found].role === live.role) targetIndex = found;
    }
    if (targetIndex < 0) {
      const explicit = explicitIdentityOf(live);
      if (explicit.length > 0) {
        targetIndex = display.findIndex(
          (candidate) => candidate.role === live.role && explicitIdentityOf(candidate).some((id) => explicit.includes(id))
        );
      }
    }
    if (targetIndex >= 0) {
      const merged = { ...display[targetIndex], ...live };
      inheritMessageIdentity(merged, display[targetIndex]);
      display[targetIndex] = merged;
    } else {
      targetIndex = display.length;
      display.push(live);
      appended += 1;
    }
    for (const key of keys) {
      indexes[key] = targetIndex;
      refs[key] = display[targetIndex];
      bindRuntimeKey(display[targetIndex], key);
    }
  }
  return { display, indexes, refs, appended };
}
function applyHistoryPageToState(s, sessionId, page, base) {
  const session = sessionOf(s, sessionId);
  if (!session) return s;
  const transcript = base ?? ensureTranscript(s.sessionTranscripts, session);
  const merged = mergeWindowPage(transcript.window, page);
  if (!merged.accepted) {
    return { historyLoading: false, initialLoading: false };
  }
  let next = {
    ...transcript,
    window: merged.window,
    serverEpoch: s.serverEpoch
  };
  if (merged.replacedEpoch) {
    next = { ...next, runtime: [], anchorOffset: merged.window.total };
  } else {
    const windowList = windowRows(next.window);
    const tracked = new Set(next.runtime);
    let mirrored = 0;
    while (mirrored < s.currentMessages.length && mirrored < windowList.length && s.currentMessages[mirrored].role === windowList[mirrored].role && s.currentMessages[mirrored].content === windowList[mirrored].content) {
      mirrored += 1;
    }
    const adopted = s.currentMessages.slice(mirrored).filter((row) => !isDurableRow(row) && !tracked.has(row));
    if (adopted.length > 0) next = { ...next, runtime: [...next.runtime, ...adopted] };
  }
  const projected = projectTranscript(next);
  const unchanged = projected.length === s.currentMessages.length && projected.every((row, index) => {
    const other = s.currentMessages[index];
    return row.role === other.role && row.content === other.content && (row.messageId ?? null) === (other.messageId ?? null) && (row.nativeItemId ?? null) === (other.nativeItemId ?? null);
  });
  const display = unchanged ? s.currentMessages : projected;
  const lastRow = windowRows(merged.window)[merged.window.rows.size - 1];
  return {
    sessions: s.sessions.map((candidate) => candidate.id === sessionId ? {
      ...candidate,
      history: canonicalHistory(display),
      historyTotal: merged.window.total,
      historyStart: merged.window.start ?? 0,
      historyTruncated: (merged.window.start ?? 0) > 0,
      historyEpoch: merged.window.epoch ?? candidate.historyEpoch,
      historyRevision: merged.window.revision,
      ...lastRow ? { lastMessage: String(lastRow.content).slice(0, 200) } : {}
    } : candidate),
    historyWindowStarts: {
      ...s.historyWindowStarts,
      [sessionId]: merged.window.start ?? 0
    },
    historyLoadEnd: merged.window.start ?? 0,
    hasMoreMessages: (merged.window.start ?? 0) > 0,
    historyLoading: false,
    initialLoading: false,
    ...s.currentSessionId === sessionId && !unchanged ? { currentMessages: display } : {},
    ...withTranscript(s, sessionId, next)
  };
}
function durableRowsOf(state, sessionId) {
  const transcript = state.sessionTranscripts[sessionId];
  if (transcript) return [...transcript.window.rows.values()];
  const session = sessionOf(state, sessionId);
  return (session?.history ?? []).filter((row) => !isLocalMarker(row));
}
function appendCanonicalRows(history, rows) {
  if (rows.length === 0) return history;
  const result = history.slice();
  const identities = new Set(result.flatMap(explicitIdentityOf));
  for (const row of rows) {
    const ids = explicitIdentityOf(row);
    if (ids.length > 0 && ids.some((id) => identities.has(id))) continue;
    const last = result[result.length - 1];
    if (!(last && last.role === row.role && last.content === row.content)) result.push(row);
    ids.forEach((id) => identities.add(id));
  }
  return result;
}
var useSessionStore = create((set, get) => ({
  sessions: [],
  sessionsLoading: false,
  currentSessionId: null,
  currentMessages: [],
  hasMoreMessages: false,
  historyLoading: false,
  initialLoading: false,
  historyLoadEnd: 0,
  multiSelectMode: false,
  selectedIds: /* @__PURE__ */ new Set(),
  inputDrafts: {},
  inputDraftRevisions: {},
  sessionUnread: {},
  rendering: false,
  _loadSeq: 0,
  _sessionWsTouchedSeq: {},
  _historyRefreshSeq: {},
  _historyPageSeq: {},
  _selectionSeq: {},
  _sessionLocalTouchedSeq: {},
  _sessionSettingsTouchedSeq: {},
  _sessionEventPatches: {},
  _deliveredQueueIds: {},
  _pendingQueueIds: {},
  liveStreamBuffers: {},
  terminalWatermarks: {},
  sessionTranscripts: {},
  sessionSettingMutations: {},
  serverEpoch: null,
  historyWindowStarts: {},
  loadSessions: async () => {
    const loadSeq = get()._loadSeq + 1;
    const transcriptBeforeLoad = { ...get().sessionTranscripts };
    const sessionsBeforeLoad = get().sessions;
    const touchedAtStart = get()._sessionWsTouchedSeq;
    const localTouchedAtStart = get()._sessionLocalTouchedSeq ?? {};
    const settingsTouchedAtStart = get()._sessionSettingsTouchedSeq ?? {};
    const eventPatchesAtStart = get()._sessionEventPatches ?? {};
    set({ _loadSeq: loadSeq, sessionsLoading: true });
    try {
      const sessions = await fetchSessions(true);
      if (get()._loadSeq !== loadSeq) return;
      const { currentSessionId } = get();
      set((s) => {
        const merged = sessions.map((sess) => {
          const sid = sess.id;
          const cur = s.sessions.find((x) => x.id === sid);
          if (!cur) return sess;
          const touchedBefore = Object.prototype.hasOwnProperty.call(
            touchedAtStart,
            sid
          );
          const touchedDuringFetch = (s._sessionWsTouchedSeq[sid] ?? 0) > (touchedAtStart[sid] ?? 0);
          const locallyTouchedDuringFetch = (s._sessionLocalTouchedSeq?.[sid] ?? 0) > (localTouchedAtStart[sid] ?? 0);
          const settingsTouchedDuringFetch = (s._sessionSettingsTouchedSeq?.[sid] ?? 0) > (settingsTouchedAtStart[sid] ?? 0);
          const snapshotIsTransientDone = sess.workerStatus === "done";
          const preserveLocalWorker = touchedDuringFetch || touchedBefore && (cur.workerStatus === null || snapshotIsTransientDone);
          let next = sess;
          const summaryHasHistory = Array.isArray(sess.history) && sess.history.length > 0;
          const hasLocalUserProjection = Boolean(
            cur && (cur.history || []).some(isLocallyOwnedUserMessage)
          );
          if (cur && !summaryHasHistory && (cur.history || []).length > 0) {
            next = {
              ...next,
              history: cur.history,
              historyTruncated: cur.historyTruncated,
              historyTotal: Math.max(
                sess.historyTotal ?? 0,
                cur.historyTotal ?? 0,
                cur.history.length
              ),
              ...hasLocalUserProjection && cur.lastMessage ? { lastMessage: cur.lastMessage } : {}
            };
          }
          if (preserveLocalWorker) {
            next = {
              ...next,
              // WS state is newer than this snapshot.  Preserve explicit null:
              // it is the destroy/crash transition, not a missing value.
              workerStatus: cur.workerStatus,
              workerId: cur.workerId
            };
          }
          const carryWorkerId = preserveLocalWorker ? cur.workerId : cur.workerId && sess.workerStatus ? cur.workerId : sess.workerId;
          if (sid === currentSessionId && cur.model) {
            next = {
              ...next,
              model: sess.model ?? cur.model,
              permissionMode: sess.permissionMode ?? cur.permissionMode,
              alwaysThinkingEnabled: sess.alwaysThinkingEnabled ?? cur.alwaysThinkingEnabled,
              effort: sess.effort || cur.effort || "",
              workdir: sess.workdir ?? cur.workdir,
              workerId: carryWorkerId
            };
          } else if (carryWorkerId && next.workerId !== carryWorkerId) {
            next = { ...next, workerId: carryWorkerId };
          }
          if (locallyTouchedDuringFetch) {
            if ((cur.historyTotal ?? 0) > (next.historyTotal ?? 0)) {
              next = { ...next, historyTotal: cur.historyTotal, lastMessage: cur.lastMessage };
            } else if (cur.lastMessage && cur.lastMessage !== next.lastMessage) {
              next = { ...next, lastMessage: cur.lastMessage };
            }
            if (cur.lastResult) next = { ...next, lastResult: cur.lastResult };
          }
          const eventPatch = s._sessionEventPatches?.[sid] ?? eventPatchesAtStart[sid];
          if (eventPatch && Object.keys(eventPatch).length > 0) {
            next = { ...next, ...eventPatch };
          }
          const mutation = s.sessionSettingMutations?.[sid];
          if (settingsTouchedDuringFetch || mutation?.pending) {
            next = mergeSessionSettingPatch(next, mutation?.patch);
            if (settingsTouchedDuringFetch && !mutation?.pending) {
              next = mergeSessionSettingPatch(next, mutation?.authoritative);
            }
          }
          next = preserveNewerSummary(cur, next);
          return sameSessionSnapshot(cur, next, sess) ? cur : next;
        });
        const consumedEventIds = new Set(sessions.map((sess) => sess.id));
        return {
          sessions: merged,
          _sessionEventPatches: Object.fromEntries(
            Object.entries(s._sessionEventPatches ?? {}).filter(([sid]) => !consumedEventIds.has(sid))
          )
        };
      });
      if (!isMockMode() && useUIStore.getState().sortBy === "custom") {
        useUIStore.getState().setCustomOrder(sessions.map((s) => s.id));
      }
      const restoreSessionId = get().currentSessionId;
      if (restoreSessionId) {
        const current = get();
        const found = current.sessions.find((s) => s.id === restoreSessionId);
        if (found) {
          if (Array.isArray(found.history) && found.history.length > 0) {
            const previousSession = sessionsBeforeLoad.find((s) => s.id === restoreSessionId);
            const base = transcriptBeforeLoad[restoreSessionId] ?? (previousSession ? {
              window: windowFromSession(previousSession),
              runtime: current.sessionTranscripts[restoreSessionId]?.runtime ?? [],
              anchorOffset: current.sessionTranscripts[restoreSessionId]?.anchorOffset ?? (previousSession.historyTotal ?? 0),
              serverEpoch: current.serverEpoch
            } : void 0);
            const windowStart = typeof found.historyStart === "number" ? found.historyStart : base?.window.start ?? 0;
            set((s) => applyHistoryPageToState(s, restoreSessionId, {
              history: found.history,
              start: windowStart,
              total: found.historyTotal ?? found.history.length,
              hasMore: !!found.historyTruncated,
              historyEpoch: found.historyEpoch,
              historyRevision: found.historyRevision
            }, base));
          }
          const after = get();
          const transcript = after.sessionTranscripts[restoreSessionId] ?? ensureTranscript(after.sessionTranscripts, found);
          set({
            hasMoreMessages: (transcript.window.start ?? 0) > 0,
            historyLoadEnd: transcript.window.start ?? 0,
            historyWindowStarts: {
              ...after.historyWindowStarts,
              [restoreSessionId]: transcript.window.start ?? 0
            }
          });
        } else {
          set({
            currentSessionId: null,
            currentMessages: [],
            hasMoreMessages: false,
            initialLoading: false
          });
        }
      }
    } catch {
      console.warn("[sessionStore] loadSessions failed");
    } finally {
      if (get()._loadSeq === loadSeq) set({ sessionsLoading: false });
    }
  },
  selectSession: async (id) => {
    const session = get().sessions.find((s) => s.id === id);
    if (!session) return;
    const loaded = (session.history || []).length;
    const needsOlder = !!session.historyTruncated;
    const selectionSeq = (get()._selectionSeq[id] ?? 0) + 1;
    const liveRows = get().liveStreamBuffers[id]?.messages ?? [];
    set((s) => {
      const existing = s.sessionTranscripts[id];
      const transcript = existing ? { ...existing, serverEpoch: s.serverEpoch } : {
        window: windowFromSession(session),
        runtime: liveRows.slice(),
        anchorOffset: session.historyTotal ?? loaded,
        serverEpoch: s.serverEpoch
      };
      const display = projectTranscript(transcript);
      const start = transcript.window.start ?? 0;
      return {
        currentSessionId: id,
        _selectionSeq: { ...s._selectionSeq, [id]: selectionSeq },
        currentMessages: display,
        hasMoreMessages: needsOlder,
        historyLoading: false,
        historyLoadEnd: start,
        historyWindowStarts: { ...s.historyWindowStarts, [id]: start },
        initialLoading: loaded === 0,
        sessionTranscripts: { ...s.sessionTranscripts, [id]: transcript }
      };
    });
    try {
      const data = await fetchSessionHistory(
        id,
        0,
        historyPageSize()
      );
      if (get().currentSessionId !== id || get()._selectionSeq[id] !== selectionSeq) {
        if (!get().currentSessionId) set({ initialLoading: false });
        return;
      }
      get().applyHistoryPage(id, data);
    } catch {
      if (get().currentSessionId === id && get()._selectionSeq[id] === selectionSeq || !get().currentSessionId) {
        set({ initialLoading: false });
      }
      console.warn("[sessionStore] selectSession fresh-history fetch failed", id);
    }
  },
  refreshCurrentSessionHistory: async () => {
    const sid = get().currentSessionId;
    if (!sid) return;
    const requestSeq = (get()._historyRefreshSeq[sid] ?? 0) + 1;
    set((s) => ({
      _historyRefreshSeq: { ...s._historyRefreshSeq, [sid]: requestSeq }
    }));
    try {
      const data = await fetchSessionHistory(sid, 0, historyPageSize());
      const current = get();
      if (current.currentSessionId !== sid || current._historyRefreshSeq[sid] !== requestSeq) return;
      current.applyHistoryPage(sid, data);
    } catch {
    }
  },
  loadOlderMessages: async () => {
    const { currentSessionId, historyLoading, historyLoadEnd } = get();
    if (historyLoading || historyLoadEnd <= 0 || !currentSessionId)
      return;
    set({ historyLoading: true });
    const sid = currentSessionId;
    const pageSeq = (get()._historyPageSeq[sid] ?? 0) + 1;
    set((s) => ({ _historyPageSeq: { ...s._historyPageSeq, [sid]: pageSeq } }));
    try {
      const data = await fetchSessionHistory(
        sid,
        historyLoadEnd,
        historyPageSize()
      );
      if (get().currentSessionId !== sid || get()._historyPageSeq[sid] !== pageSeq) {
        if (get().currentSessionId === sid) set({ historyLoading: false });
        return;
      }
      const msgs = data.history || [];
      if (msgs.length === 0) {
        set({ historyLoading: false });
        return;
      }
      get().applyHistoryPage(sid, data);
      if (get()._historyPageSeq[sid] === pageSeq) set({ historyLoading: false });
    } catch {
      if (get().currentSessionId === sid && get()._historyPageSeq[sid] === pageSeq) {
        set({ historyLoading: false });
      }
    }
  },
  createNewSession: async (name, workdir, adapter, sessionTemplate, settings) => {
    const placeholder = {
      id: `__pending_${name}`,
      name: "...",
      adapter: adapter || "cbc",
      model: settings?.model ?? null,
      permissionMode: settings?.permissionMode ?? null,
      alwaysThinkingEnabled: settings?.alwaysThinkingEnabled ?? false,
      effort: settings?.effort || "",
      history: []
    };
    set((s) => ({
      sessions: [...s.sessions, placeholder],
      currentSessionId: placeholder.id,
      // 新建会话的聊天面板必须立刻清空旧 session 的消息：currentMessages
      // 不会被下方 set 自动重置，若不在此清空，左侧卡片已切到新 session 但
      // 聊天区仍渲染旧 session 内容，需反复切换才刷新（bug 1）。
      currentMessages: [],
      // 新 session 没有 history 需要拉取——清掉可能残留的 initialLoading，
      // 否则空历史的新会话会一直显示转圈而非空态。
      initialLoading: false
    }));
    try {
      const session = await createSession(
        name,
        workdir,
        adapter,
        sessionTemplate,
        settings
      );
      set((s) => {
        const withoutPlaceholder = s.sessions.filter(
          (se) => se.id !== placeholder.id
        );
        const sessions = withoutPlaceholder.some((se) => se.id === session.id) ? withoutPlaceholder : [...withoutPlaceholder, session];
        const wasCurrent = s.currentSessionId === placeholder.id || s.currentSessionId === null;
        return {
          sessions,
          currentSessionId: wasCurrent ? session.id : s.currentSessionId,
          // 真实 session 就绪后，聊天区同步为新 session 的历史（新建为空）。
          // 否则 currentMessages 仍残留上一 session 的内容（bug 1）。
          // 仅当创建流程仍是当前选中时才覆盖——若用户中途切走，保持其当前
          // session 的消息不变，避免把别的 session 的消息区清空。
          currentMessages: wasCurrent ? session.history || [] : s.currentMessages,
          initialLoading: false
        };
      });
    } catch (e) {
      set((s) => ({
        sessions: s.sessions.filter((se) => se.id !== placeholder.id),
        initialLoading: false
      }));
      throw e;
    }
  },
  removeSession: async (id) => {
    if (id.startsWith("__pending_")) return;
    set((s) => ({
      sessions: s.sessions.filter((session) => session.id !== id),
      currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
      currentMessages: s.currentSessionId === id ? [] : s.currentMessages,
      _deliveredQueueIds: Object.fromEntries(
        Object.entries(s._deliveredQueueIds ?? {}).filter(([sid]) => sid !== id)
      ),
      _sessionLocalTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionLocalTouchedSeq ?? {}).filter(([sid]) => sid !== id)
      ),
      _sessionSettingsTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionSettingsTouchedSeq ?? {}).filter(([sid]) => sid !== id)
      ),
      sessionSettingMutations: Object.fromEntries(
        Object.entries(s.sessionSettingMutations ?? {}).filter(([sid]) => sid !== id)
      ),
      _sessionEventPatches: Object.fromEntries(
        Object.entries(s._sessionEventPatches ?? {}).filter(([sid]) => sid !== id)
      )
    }));
    try {
      await deleteSession(id);
      if (get().currentSessionId === id) {
        set({ currentSessionId: null, currentMessages: [] });
      }
      await get().loadSessions();
    } catch {
      await get().loadSessions();
    }
  },
  removeSessions: async (ids, cascadeIds = []) => {
    const selected = new Set(ids.filter((id) => !id.startsWith("__pending_")));
    if (selected.size === 0) return;
    set((s) => ({
      sessions: s.sessions.filter((session) => !selected.has(session.id)),
      currentSessionId: s.currentSessionId && selected.has(s.currentSessionId) ? null : s.currentSessionId,
      currentMessages: s.currentSessionId && selected.has(s.currentSessionId) ? [] : s.currentMessages,
      selectedIds: /* @__PURE__ */ new Set(),
      multiSelectMode: false,
      _deliveredQueueIds: Object.fromEntries(
        Object.entries(s._deliveredQueueIds ?? {}).filter(([sid]) => !selected.has(sid))
      ),
      _sessionLocalTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionLocalTouchedSeq ?? {}).filter(([sid]) => !selected.has(sid))
      ),
      _sessionSettingsTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionSettingsTouchedSeq ?? {}).filter(([sid]) => !selected.has(sid))
      ),
      sessionSettingMutations: Object.fromEntries(
        Object.entries(s.sessionSettingMutations ?? {}).filter(([sid]) => !selected.has(sid))
      ),
      _sessionEventPatches: Object.fromEntries(
        Object.entries(s._sessionEventPatches ?? {}).filter(([sid]) => !selected.has(sid))
      )
    }));
    try {
      await batchDeleteSessions([...selected], cascadeIds.filter((id) => selected.has(id)));
      await get().loadSessions();
    } catch {
      await get().loadSessions();
    }
  },
  batchRemoveSessions: async () => {
    const { selectedIds } = get();
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    set((s) => ({
      sessions: s.sessions.filter(
        (session) => !selectedIds.has(session.id)
      ),
      multiSelectMode: false,
      selectedIds: /* @__PURE__ */ new Set(),
      _sessionSettingsTouchedSeq: Object.fromEntries(
        Object.entries(s._sessionSettingsTouchedSeq ?? {}).filter(([sid]) => !selectedIds.has(sid))
      ),
      sessionSettingMutations: Object.fromEntries(
        Object.entries(s.sessionSettingMutations ?? {}).filter(([sid]) => !selectedIds.has(sid))
      )
    }));
    const { currentSessionId } = get();
    if (currentSessionId && selectedIds.has(currentSessionId)) {
      set({ currentSessionId: null, currentMessages: [] });
    }
    try {
      await batchDeleteSessions(ids);
      await get().loadSessions();
    } catch {
      await get().loadSessions();
    }
  },
  rename: async (id, name) => {
    await renameSession(id, name);
    set((s) => ({
      sessions: s.sessions.map(
        (session) => session.id === id ? { ...session, name } : session
      )
    }));
  },
  branch: async (id, name) => {
    await branchSession(id, name);
    await get().loadSessions();
  },
  reimport: async (id) => {
    const session = get().sessions.find((s) => s.id === id);
    if (!session?.cliSessionId) return;
    const newSession = await reimportSession(
      id,
      session.adapter || "cbc",
      session.cliSessionId,
      session.workdir
    );
    set((s) => ({
      sessions: s.sessions.map(
        (session2) => session2.id === id ? newSession : session2
      ),
      currentSessionId: s.currentSessionId === id ? newSession.id : s.currentSessionId,
      initialLoading: false
    }));
  },
  setInputDraft: (id, draft) => {
    set((s) => ({
      inputDrafts: { ...s.inputDrafts, [id]: draft },
      inputDraftRevisions: {
        ...s.inputDraftRevisions ?? {},
        [id]: (s.inputDraftRevisions?.[id] ?? 0) + 1
      }
    }));
  },
  acceptServerEpoch: (epoch) => {
    if (!epoch) return;
    set((s) => {
      if (s.serverEpoch === epoch) return s;
      const runtimeMessages = Object.values(s.liveStreamBuffers).flatMap(
        (buffer) => buffer.messages
      );
      const staleIds = new Set(runtimeMessages.flatMap(explicitMessageIdentity));
      const runtimeRefs = new Set(runtimeMessages);
      const runtimeShapes = new Set(runtimeMessages.map(messageShapeKey));
      const durableMessages = s.currentSessionId ? durableRowsOf(s, s.currentSessionId) : [];
      const durableRefs = new Set(durableMessages);
      const durableIds = new Set(durableMessages.flatMap(explicitMessageIdentity));
      const durableShapeCounts = /* @__PURE__ */ new Map();
      for (const message of durableMessages) {
        const key = messageShapeKey(message);
        durableShapeCounts.set(key, (durableShapeCounts.get(key) ?? 0) + 1);
      }
      const retainedDurableShapes = /* @__PURE__ */ new Map();
      const sessionTranscripts = Object.fromEntries(
        Object.entries(s.sessionTranscripts).map(([sid, transcript]) => {
          const session = s.sessions.find((candidate) => candidate.id === sid);
          return [
            sid,
            {
              ...transcript,
              runtime: [],
              anchorOffset: session?.historyTotal ?? transcript.window.total ?? transcript.anchorOffset,
              serverEpoch: epoch
            }
          ];
        })
      );
      const display = s.currentMessages.filter((message) => {
        if (isLocalMarker(message)) return true;
        if (isDurableRow(message)) return true;
        const ids = explicitMessageIdentity(message);
        if (runtimeRefs.has(message) && !durableRefs.has(message)) return false;
        if (ids.some((id) => staleIds.has(id)) && !ids.some((id) => durableIds.has(id))) return false;
        const shape = messageShapeKey(message);
        if (!runtimeShapes.has(shape)) return true;
        const durableCount = durableShapeCounts.get(shape) ?? 0;
        const retained = retainedDurableShapes.get(shape) ?? 0;
        if (retained < durableCount) {
          retainedDurableShapes.set(shape, retained + 1);
          return true;
        }
        return false;
      });
      return {
        serverEpoch: epoch,
        liveStreamBuffers: {},
        terminalWatermarks: {},
        _sessionEventPatches: {},
        ...runtimeMessages.length > 0 ? {
          currentMessages: display,
          ...s.currentSessionId ? { sessions: mirrorHistory(s.sessions, s.currentSessionId, display) } : {}
        } : {},
        sessionTranscripts
      };
    });
  },
  addMessage: (msg) => {
    const touchSeq = localTouchSeq += 1;
    set((s) => {
      const sid = s.currentSessionId;
      if (msg.role === "system") markLocalMarker(msg);
      const row = msg.role === "system" ? bindRuntimeKey(msg, `marker:${localTouchSeq}`) : msg;
      if (!sid) return { currentMessages: [...s.currentMessages, row] };
      const session = sessionOf(s, sid);
      const base = session ? ensureTranscript(s.sessionTranscripts, session) : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const display = [...s.currentMessages, row];
      return {
        currentMessages: display,
        sessions: mirrorHistory(s.sessions, sid, display),
        ...withTranscript(s, sid, { ...base, runtime: [...base.runtime, row] }),
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sid]: touchSeq
        }
      };
    });
  },
  appendMessages: (msgs) => {
    if (!msgs.length) return;
    const touchSeq = localTouchSeq += 1;
    set((s) => {
      const sid = s.currentSessionId;
      const rows = msgs.map((msg, index) => {
        if (msg.role !== "system") return msg;
        markLocalMarker(msg);
        return bindRuntimeKey(msg, `marker:${localTouchSeq}:${index}`);
      });
      if (!sid) return { currentMessages: [...s.currentMessages, ...rows] };
      const session = sessionOf(s, sid);
      const base = session ? ensureTranscript(s.sessionTranscripts, session) : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const display = [...s.currentMessages, ...rows];
      return {
        currentMessages: display,
        sessions: mirrorHistory(s.sessions, sid, display),
        ...withTranscript(s, sid, { ...base, runtime: [...base.runtime, ...rows] }),
        _sessionLocalTouchedSeq: { ...s._sessionLocalTouchedSeq, [sid]: touchSeq }
      };
    });
  },
  appendLocalMessage: (sessionId, message) => {
    if (!sessionId || message.role !== "user" || !message.content) return;
    const localMessage = withLocalUserIdentity(sessionId, message);
    const touchSeq = localTouchSeq += 1;
    set((s) => {
      const target = s.sessions.find((session) => session.id === sessionId);
      if (!target) return s;
      const history = target.history || [];
      const alreadyInHistory = history.some(
        (candidate) => hasExplicitIdentityOverlap(candidate, localMessage)
      );
      const historyTotal = target.historyTotal ?? history.length;
      rememberLocalMessageOrigin(localMessage, historyTotal);
      const nextHistory = alreadyInHistory ? history : [...history, localMessage];
      const sessions = s.sessions.map((session) => session.id === sessionId ? {
        ...session,
        history: nextHistory,
        historyTotal: Math.max(
          session.historyTotal ?? history.length,
          nextHistory.length,
          historyTotal + (alreadyInHistory ? 0 : 1)
        ),
        lastMessage: localMessage.content.slice(0, 200)
      } : session);
      const alreadyCurrent = s.currentMessages.some(
        (candidate) => hasExplicitIdentityOverlap(candidate, localMessage)
      );
      const appendHere = s.currentSessionId === sessionId && !alreadyCurrent;
      const display = appendHere ? [...s.currentMessages, localMessage] : s.currentMessages;
      const base = ensureTranscript(s.sessionTranscripts, target);
      return {
        sessions,
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: touchSeq
        },
        ...appendHere ? {
          currentMessages: display,
          ...withTranscript(s, sessionId, {
            ...base,
            runtime: [...base.runtime, bindRuntimeKey(localMessage, `local:${localTouchSeq}`)]
          })
        } : {}
      };
    });
  },
  getLiveStreamMessages: (sessionId) => (get().liveStreamBuffers[sessionId]?.messages || []).slice(),
  canApplyLiveStream: (sessionId, meta) => {
    const state = get();
    const terminal = state.terminalWatermarks[sessionId];
    if (isBlockedByTerminal(meta, terminal, "stream")) return false;
    const previous = state.liveStreamBuffers[sessionId];
    return !previous || !isOlderMeta(meta, previous);
  },
  applyLiveStream: (sessionId, messages, meta) => {
    if (!messages.length) return false;
    let accepted = false;
    set((s) => {
      const terminal = s.terminalWatermarks[sessionId];
      if (isBlockedByTerminal(meta, terminal, "stream")) return s;
      const previous = s.liveStreamBuffers[sessionId];
      if (previous && isOlderMeta(meta, previous)) return s;
      const revision = Math.max(
        previous?.revision ?? 0,
        terminal?.revision ?? 0
      ) + 1;
      const taskKey = taskScopeKey(sessionId, meta, previous);
      const buffer = {
        ...meta,
        taskKey,
        revision,
        messages: messages.slice()
      };
      accepted = true;
      const session = sessionOf(s, sessionId);
      const base = session ? ensureTranscript(s.sessionTranscripts, session) : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const projected = s.currentSessionId === sessionId ? projectLiveRows(s.currentMessages, previous, taskKey, buffer.messages) : { display: s.currentMessages, indexes: {}, refs: {}, appended: 0 };
      buffer.projectionIndexes = projected.indexes;
      buffer.projectionRefs = projected.refs;
      const previousLiveKeys = (previous?.messages ?? []).map(
        (row, slot) => liveProjectionKeys(row, { taskKey: previous?.taskKey ?? taskKey, slot })[0]
      );
      const runtime = base.runtime.filter((row) => {
        const key = runtimeKeyOf(row);
        return key === null || !previousLiveKeys.includes(key);
      });
      const nextRuntime = buffer.messages.map((row, slot) => {
        const key = liveProjectionKeys(row, { taskKey, slot })[0];
        return bindRuntimeKey(row, key);
      });
      const transcript = {
        ...base,
        runtime: [...runtime, ...nextRuntime]
      };
      if (s.currentSessionId === sessionId) {
        return {
          liveStreamBuffers: { ...s.liveStreamBuffers, [sessionId]: buffer },
          currentMessages: projected.display,
          sessions: mirrorHistory(s.sessions, sessionId, projected.display),
          ...withTranscript(s, sessionId, transcript)
        };
      }
      return {
        liveStreamBuffers: { ...s.liveStreamBuffers, [sessionId]: buffer },
        ...withTranscript(s, sessionId, transcript)
      };
    });
    return accepted;
  },
  reconcileWorkerResult: (sessionId, event, meta) => {
    let accepted = false;
    let needsRecovery = false;
    set((s) => {
      const terminal = s.terminalWatermarks[sessionId];
      const previousBuffer = s.liveStreamBuffers[sessionId];
      const result = typeof event.result === "string" ? event.result : "";
      const hasResult = result.trim().length > 0;
      const status = event.status === "error" ? "error" : event.status === "cancelled" || event.cancelled ? "cancelled" : "done";
      const incomingTaskKey = taskScopeKey(sessionId, meta, previousBuffer);
      if (terminal) {
        if (isOlderMeta(meta, terminal)) return s;
        const sameCursor = terminal.taskSeq !== void 0 && meta.taskSeq !== void 0 && terminal.taskSeq === meta.taskSeq;
        const noCursor = terminal.taskSeq === void 0 && meta.taskSeq === void 0 && terminal.taskId === void 0 && meta.taskId === void 0;
        if (sameCursor || terminal.taskKey === incomingTaskKey) return s;
        if (noCursor && terminal.result === result) return s;
      }
      if (previousBuffer && isOlderMeta(meta, previousBuffer)) return s;
      const coverage = event.terminalCoverage ?? {
        ...typeof event.historyEpoch === "string" ? { historyEpoch: event.historyEpoch } : {},
        ...typeof event.historyRevision === "number" ? { historyRevision: event.historyRevision } : {}
      };
      if (coverage.historyEpoch || typeof coverage.historyRevision === "number") {
        needsRecovery = true;
      }
      const session = sessionOf(s, sessionId);
      const base = session ? ensureTranscript(s.sessionTranscripts, session) : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const liveMessages = previousBuffer?.messages ?? [];
      let finalSlot = -1;
      for (let index = liveMessages.length - 1; index >= 0; index -= 1) {
        if (liveMessages[index].role === "assistant") {
          finalSlot = index;
          break;
        }
      }
      let finalized = liveMessages;
      let appendedResult = false;
      if (hasResult) {
        finalized = liveMessages.map((message, index) => {
          const next = index === finalSlot ? { ...message, content: result } : { ...message };
          inheritMessageIdentity(next, message);
          return next;
        });
        if (finalSlot < 0) {
          const row = {
            role: "assistant",
            content: result,
            ...meta.turnId ? { nativeItemId: `turn:${meta.turnId}` } : {}
          };
          finalized = [...finalized, row];
          appendedResult = true;
        }
      }
      const revision = Math.max(
        previousBuffer?.revision ?? 0,
        terminal?.revision ?? 0
      ) + 1;
      let convergedReplay = false;
      if (typeof coverage.historyRevision === "number" && base.window.revision >= coverage.historyRevision && finalized.length > 0 && !appendedResult) {
        const start = base.anchorOffset - finalized.length;
        if (start >= 0) {
          let allMatch = true;
          for (let index = 0; index < finalized.length; index += 1) {
            const durable = base.window.rows.get(start + index);
            const row = finalized[index];
            if (!durable || durable.role !== row.role || durable.content !== row.content) {
              allMatch = false;
              break;
            }
          }
          convergedReplay = allMatch;
        }
      }
      if (convergedReplay) {
        needsRecovery = false;
      }
      const previousLiveKeys = liveMessages.map(
        (row, slot) => liveProjectionKeys(row, { taskKey: previousBuffer?.taskKey ?? incomingTaskKey, slot })[0]
      );
      const keptRuntime = base.runtime.filter((row) => {
        const key = runtimeKeyOf(row);
        return key === null || !previousLiveKeys.includes(key);
      });
      const finalizedRuntime = convergedReplay ? [] : finalized.map(
        (row, slot) => bindRuntimeKey(row, liveProjectionKeys(row, { taskKey: incomingTaskKey, slot })[0])
      );
      let nextTranscript = {
        ...base,
        runtime: [...keptRuntime, ...finalizedRuntime]
      };
      const isCurrent = s.currentSessionId === sessionId;
      if (isCurrent && !convergedReplay) {
        const tracked = new Set(nextTranscript.runtime);
        const trackedKeys = new Set(
          nextTranscript.runtime.map((row) => runtimeKeyOf(row)).filter((key) => key !== null)
        );
        const windowList = windowRows(nextTranscript.window);
        let mirrored = 0;
        while (mirrored < s.currentMessages.length && mirrored < windowList.length && s.currentMessages[mirrored].role === windowList[mirrored].role && s.currentMessages[mirrored].content === windowList[mirrored].content) {
          mirrored += 1;
        }
        const adopted = s.currentMessages.slice(mirrored).filter((row) => {
          if (isDurableRow(row) || tracked.has(row)) return false;
          const key = runtimeKeyOf(row);
          return key === null || !trackedKeys.has(key);
        });
        if (adopted.length > 0) {
          nextTranscript = { ...nextTranscript, runtime: [...adopted, ...nextTranscript.runtime] };
        }
      }
      const display = isCurrent && finalized.length > 0 ? projectTranscript(nextTranscript) : s.currentMessages;
      const sessions = s.sessions.map((candidate) => {
        if (candidate.id !== sessionId) return candidate;
        const history = isCurrent ? canonicalHistory(display) : appendCanonicalRows(candidate.history ?? [], finalized);
        return {
          ...candidate,
          history,
          historyTotal: Math.max(
            candidate.historyTotal ?? history.length,
            history.length,
            (candidate.historyTotal ?? 0) + (appendedResult ? 1 : 0)
          ),
          lastMessage: hasResult ? result.slice(0, 200) : candidate.lastMessage,
          lastResult: {
            status,
            result,
            timestamp: (/* @__PURE__ */ new Date()).toISOString(),
            ...meta.taskSeq !== void 0 ? { taskSeq: meta.taskSeq } : {}
          }
        };
      });
      accepted = true;
      const needsRecoveryFlag = needsRecovery;
      if (needsRecoveryFlag) {
        queueMicrotask(() => {
          void get().recoverSessionHistory(sessionId);
        });
      }
      return {
        sessions,
        liveStreamBuffers: Object.fromEntries(
          Object.entries(s.liveStreamBuffers).filter(([id]) => id !== sessionId)
        ),
        terminalWatermarks: {
          ...s.terminalWatermarks,
          [sessionId]: {
            ...meta,
            taskKey: incomingTaskKey,
            status,
            revision,
            result,
            ...coverage.historyEpoch ? { historyEpoch: coverage.historyEpoch } : {},
            ...typeof coverage.historyRevision === "number" ? { historyRevision: coverage.historyRevision } : {}
          }
        },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: localTouchSeq += 1
        },
        ...s.currentSessionId === sessionId ? { currentMessages: display } : {},
        ...withTranscript(s, sessionId, nextTranscript)
      };
    });
    return accepted;
  },
  /**
   * Apply an authoritative history page to a Session transcript.
   *
   * Shared by focus refresh, lazy paging, session entry and terminal recovery so
   * there is exactly one place that decides which canonical rows are loaded.
   */
  applyHistoryPage: (sessionId, page) => {
    let accepted = false;
    set((s) => {
      const session = sessionOf(s, sessionId);
      if (!session) return s;
      const base = ensureTranscript(s.sessionTranscripts, session);
      accepted = mergeWindowPage(base.window, page).accepted;
      return applyHistoryPageToState(s, sessionId, page);
    });
    return accepted;
  },
  /** Recover the canonical tail after a terminal event's coverage boundary. */
  recoverSessionHistory: async (sessionId) => {
    const requestSeq = (get()._historyRefreshSeq[sessionId] ?? 0) + 1;
    set((s) => ({
      _historyRefreshSeq: { ...s._historyRefreshSeq, [sessionId]: requestSeq }
    }));
    try {
      const data = await fetchSessionHistory(sessionId, 0, historyPageSize());
      if (get()._historyRefreshSeq[sessionId] !== requestSeq) return;
      get().applyHistoryPage(sessionId, data);
    } catch {
    }
  },
  applyWorkerStatus: (sessionId, status, meta, terminal = false) => {
    if (!sessionId) return false;
    let accepted = false;
    set((s) => {
      const watermark = s.terminalWatermarks[sessionId];
      if (isBlockedByTerminal(meta, watermark, status || "idle")) return s;
      const previous = s.liveStreamBuffers[sessionId];
      if (previous && (meta.taskSeq !== void 0 || previous.taskSeq !== void 0) && isOlderMeta(meta, previous)) return s;
      if (previous && status === "running" && meta.taskSeq !== void 0 && previous.taskSeq !== void 0 && meta.taskSeq > previous.taskSeq) {
        const revision = Math.max(
          previous.revision,
          s.terminalWatermarks[sessionId]?.revision ?? 0
        ) + 1;
        const nextBuffers = { ...s.liveStreamBuffers };
        nextBuffers[sessionId] = {
          workerId: meta.workerId,
          generation: meta.generation,
          taskSeq: meta.taskSeq,
          taskId: meta.taskId,
          revision,
          messages: []
        };
        accepted = true;
        return {
          liveStreamBuffers: nextBuffers,
          sessions: s.sessions.map((session) => session.id === sessionId ? { ...session, workerId: meta.workerId ?? session.workerId, workerStatus: status } : session),
          _sessionWsTouchedSeq: { ...s._sessionWsTouchedSeq, [sessionId]: wsTouchSeq += 1 }
        };
      }
      accepted = true;
      const next = {
        sessions: s.sessions.map((session) => session.id === sessionId ? {
          ...session,
          workerId: terminal ? null : meta.workerId ?? session.workerId,
          workerStatus: status
        } : session),
        _sessionWsTouchedSeq: { ...s._sessionWsTouchedSeq, [sessionId]: wsTouchSeq += 1 }
      };
      if (terminal) {
        const revision = Math.max(
          previous?.revision ?? 0,
          watermark?.revision ?? 0
        ) + 1;
        const nextBuffers = { ...s.liveStreamBuffers };
        delete nextBuffers[sessionId];
        return {
          ...next,
          liveStreamBuffers: nextBuffers,
          terminalWatermarks: {
            ...s.terminalWatermarks,
            [sessionId]: { ...meta, status: status || "terminal", revision }
          }
        };
      }
      return next;
    });
    return accepted;
  },
  clearLiveStream: (sessionId) => {
    set((s) => {
      if (!s.liveStreamBuffers[sessionId]) return s;
      const liveStreamBuffers = { ...s.liveStreamBuffers };
      delete liveStreamBuffers[sessionId];
      return { liveStreamBuffers };
    });
  },
  appendQueuedMessage: (sessionId, item) => {
    if (!item.id || !item.text.trim()) return;
    const touchSeq = localTouchSeq += 1;
    const localMessage = {
      role: "user",
      content: item.text,
      ...item.parts ? { parts: item.parts } : {},
      queueItemIds: [item.id]
    };
    set((s) => {
      const target = s.sessions.find((session) => session.id === sessionId);
      if (!target) return s;
      const history = target.history || [];
      const historyHasItem = history.some(
        (message) => queueIds(message).some((id) => queueIdMatches(id, item.id))
      );
      const currentHasItem = s.currentSessionId === sessionId && s.currentMessages.some(
        (message) => queueIds(message).some((id) => queueIdMatches(id, item.id))
      );
      const historyTotal = target.historyTotal ?? history.length;
      rememberLocalMessageOrigin(localMessage, historyTotal);
      const nextHistory = historyHasItem ? history : [...history, localMessage];
      const existingHistoryMessage = history.find(
        (message) => queueIds(message).some((id) => queueIdMatches(id, item.id))
      );
      const currentMessage = existingHistoryMessage ?? localMessage;
      const sessions = s.sessions.map((session) => session.id === sessionId ? {
        ...session,
        history: nextHistory,
        historyTotal: Math.max(
          session.historyTotal ?? history.length,
          nextHistory.length,
          historyTotal + (historyHasItem ? 0 : 1)
        ),
        lastMessage: item.text.slice(0, 200)
      } : session);
      return {
        sessions,
        _pendingQueueIds: {
          ...s._pendingQueueIds,
          [sessionId]: /* @__PURE__ */ new Set([
            ...s._pendingQueueIds[sessionId] ?? [],
            canonicalQueueId(item.id)
          ])
        },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: touchSeq
        },
        ...s.currentSessionId === sessionId && !currentHasItem ? { currentMessages: [...s.currentMessages, currentMessage] } : {}
      };
    });
  },
  updateQueuedMessage: (sessionId, item) => {
    if (!sessionId || !item.id || !item.text.trim()) return;
    const update = (message, pendingIds) => {
      if (!queueIds(message).some((id) => queueSetMatches(pendingIds, id))) return message;
      if (!queueIds(message).some((id) => queueIdMatches(id, item.id))) return message;
      const next = {
        ...message,
        content: item.text,
        ...item.parts ? { parts: item.parts } : {}
      };
      if (!item.parts) delete next.parts;
      copyLocalMessageOrigin(next, message);
      inheritMessageIdentity(next, message);
      return next;
    };
    set((s) => {
      const target = s.sessions.find((session) => session.id === sessionId);
      if (!target) return s;
      const pendingIds = s._pendingQueueIds[sessionId] ?? /* @__PURE__ */ new Set();
      const history = (target.history || []).map((message) => update(message, pendingIds));
      const currentMessages = s.currentSessionId === sessionId ? s.currentMessages.map((message) => update(message, pendingIds)) : s.currentMessages;
      const changed = history.some((message, index) => message !== target.history?.[index]) || s.currentSessionId === sessionId && currentMessages.some((message, index) => message !== s.currentMessages[index]);
      if (!changed) return s;
      return {
        sessions: s.sessions.map((session) => session.id === sessionId ? { ...session, history, lastMessage: item.text.slice(0, 200) } : session),
        ...s.currentSessionId === sessionId ? { currentMessages } : {},
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: localTouchSeq += 1
        }
      };
    });
  },
  removeQueuedMessage: (sessionId, queueItemId) => {
    if (!sessionId || !queueItemId) return;
    set((s) => {
      const removePending = (message) => {
        const pendingIds = s._pendingQueueIds[sessionId] ?? /* @__PURE__ */ new Set();
        if (!queueIds(message).some((id) => queueSetMatches(pendingIds, id))) return message;
        if (!queueIds(message).some((id) => queueIdMatches(id, queueItemId))) return message;
        return null;
      };
      const target = s.sessions.find((session) => session.id === sessionId);
      if (!target) return s;
      const history = (target.history || []).map(removePending).filter(
        (message) => message !== null
      );
      const currentMessages = s.currentSessionId === sessionId ? s.currentMessages.map(removePending).filter(
        (message) => message !== null
      ) : s.currentMessages;
      const nextPendingIds = new Set(s._pendingQueueIds[sessionId] ?? []);
      for (const pendingId of nextPendingIds) {
        if (queueIdMatches(pendingId, queueItemId)) nextPendingIds.delete(pendingId);
      }
      if (history.length === (target.history || []).length && currentMessages.length === s.currentMessages.length && nextPendingIds.size === (s._pendingQueueIds[sessionId] ?? /* @__PURE__ */ new Set()).size) return s;
      return {
        sessions: s.sessions.map((session) => session.id === sessionId ? { ...session, history } : session),
        ...s.currentSessionId === sessionId ? { currentMessages } : {},
        _pendingQueueIds: { ...s._pendingQueueIds, [sessionId]: nextPendingIds },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: localTouchSeq += 1
        }
      };
    });
  },
  appendDeliveredMessages: (sessionId, msgs) => {
    if (!msgs.length) return;
    const touchSeq = localTouchSeq += 1;
    const localMessages = msgs.map((message) => ({
      ...message.role === "user" ? withLocalUserIdentity(sessionId, message) : message
    }));
    set((s) => {
      const pendingIds = s._pendingQueueIds[sessionId] ?? /* @__PURE__ */ new Set();
      const selected = s.currentSessionId === sessionId;
      const selectedMessages = selected ? [...s.currentMessages] : [];
      const existingIds = new Set(
        selectedMessages.flatMap((message) => explicitMessageIdentity(message))
      );
      const currentAppend = [];
      let currentChanged = false;
      for (const message of localMessages) {
        const ids = explicitMessageIdentity(message);
        const queueMatch = selectedMessages.findIndex(
          (candidate) => queueIds(candidate).some(
            (candidateId) => queueIds(message).some((incomingId) => queueIdMatches(candidateId, incomingId))
          )
        );
        if (queueMatch >= 0 && queueIds(selectedMessages[queueMatch]).some((id) => queueSetMatches(pendingIds, id))) {
          const updated = { ...selectedMessages[queueMatch], ...message };
          inheritMessageIdentity(updated, selectedMessages[queueMatch]);
          selectedMessages[queueMatch] = updated;
          currentChanged = true;
          continue;
        }
        if (ids.some((id) => existingIds.has(id))) continue;
        currentAppend.push(message);
        ids.forEach((id) => existingIds.add(id));
      }
      const deliveredIds = new Set(s._deliveredQueueIds?.[sessionId] ?? []);
      const previouslyDelivered = new Set(deliveredIds);
      localMessages.flatMap((message) => queueIds(message)).forEach((id) => deliveredIds.add(canonicalQueueId(id)));
      const sessions = s.sessions.map((session) => {
        if (session.id !== sessionId) return session;
        const history = session.history || [];
        const nextHistory = history.slice();
        const historyIds = new Set(history.flatMap((message) => explicitMessageIdentity(message)));
        const historyAppend = [];
        let added = 0;
        for (const message of localMessages) {
          const ids = explicitMessageIdentity(message);
          const queueMatch = nextHistory.findIndex(
            (candidate) => queueIds(candidate).some(
              (candidateId) => queueIds(message).some((incomingId) => queueIdMatches(candidateId, incomingId))
            )
          );
          if (queueMatch >= 0 && queueIds(nextHistory[queueMatch]).some((id) => queueSetMatches(pendingIds, id))) {
            const updated = { ...nextHistory[queueMatch], ...message };
            inheritMessageIdentity(updated, nextHistory[queueMatch]);
            nextHistory[queueMatch] = updated;
            continue;
          }
          if (queueIds(message).some((id) => queueSetMatches(previouslyDelivered, id))) continue;
          if (ids.some((id) => historyIds.has(id))) continue;
          rememberLocalMessageOrigin(
            message,
            (session.historyTotal ?? history.length) + added
          );
          historyAppend.push(message);
          ids.forEach((id) => historyIds.add(id));
          added += 1;
        }
        nextHistory.push(...historyAppend);
        const last = localMessages[localMessages.length - 1];
        return {
          ...session,
          history: nextHistory,
          historyTotal: Math.max(
            (session.historyTotal ?? history.length) + added,
            nextHistory.length
          ),
          lastMessage: last?.content.slice(0, 200) ?? session.lastMessage
        };
      });
      const base = sessionOf(s, sessionId) ? ensureTranscript(s.sessionTranscripts, sessionOf(s, sessionId)) : { window: createWindow(), runtime: [], anchorOffset: 0, serverEpoch: null };
      const runtimeRows = currentAppend.length > 0 ? currentAppend.map(
        (row, index) => bindRuntimeKey(row, `delivered:${touchSeq}:${index}`)
      ) : [];
      return {
        sessions,
        _pendingQueueIds: {
          ...s._pendingQueueIds,
          [sessionId]: new Set(
            [...pendingIds].filter((pendingId) => !queueSetMatches(deliveredIds, pendingId))
          )
        },
        _deliveredQueueIds: {
          ...s._deliveredQueueIds ?? {},
          [sessionId]: deliveredIds
        },
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [sessionId]: touchSeq
        },
        ...selected && (currentAppend.length || currentChanged) ? { currentMessages: [...selectedMessages, ...currentAppend] } : {},
        ...runtimeRows.length > 0 ? withTranscript(s, sessionId, { ...base, runtime: [...base.runtime, ...runtimeRows] }) : {}
      };
    });
  },
  patchSessionSettings: async (sessionId, patch, persist) => {
    const current = get().sessions.find((session) => session.id === sessionId);
    if (!current) throw new Error("Session not found");
    const previous = get().sessionSettingMutations[sessionId];
    const sequence = (previous?.sequence ?? 0) + 1;
    const rollback = pickSessionSettings(current);
    const authoritative = previous?.authoritative ?? rollback;
    const touchSeq = settingsTouchSeq += 1;
    set((s) => ({
      sessions: s.sessions.map(
        (session) => session.id === sessionId ? mergeSessionSettingPatch(session, patch) : session
      ),
      sessionSettingMutations: {
        ...s.sessionSettingMutations,
        [sessionId]: {
          sequence,
          pending: true,
          patch,
          rollback,
          authoritative,
          error: null
        }
      },
      _sessionSettingsTouchedSeq: {
        ...s._sessionSettingsTouchedSeq,
        [sessionId]: touchSeq
      }
    }));
    try {
      const response = await persist(sessionId, patch);
      const responsePatch = response && "id" in response && response.id === sessionId ? pickSessionSettings(response) : {};
      const latest = get().sessionSettingMutations[sessionId];
      if (!latest || latest.sequence !== sequence) {
        if (latest?.pending && Object.keys(responsePatch).length > 0) {
          set((s) => ({
            sessionSettingMutations: {
              ...s.sessionSettingMutations,
              [sessionId]: { ...latest, authoritative: responsePatch }
            }
          }));
        }
        return { response, applied: false, stale: true };
      }
      const settled = { ...patch, ...responsePatch };
      const settledTouchSeq = settingsTouchSeq += 1;
      set((s) => ({
        sessions: s.sessions.map(
          (session) => session.id === sessionId ? mergeSessionSettingPatch(session, settled) : session
        ),
        sessionSettingMutations: {
          ...s.sessionSettingMutations,
          [sessionId]: {
            ...latest,
            pending: false,
            patch: void 0,
            authoritative: settled,
            error: null
          }
        },
        _sessionSettingsTouchedSeq: {
          ...s._sessionSettingsTouchedSeq,
          [sessionId]: settledTouchSeq
        }
      }));
      return { response, applied: true, stale: false };
    } catch (error) {
      const message = errorMessage(error);
      const latest = get().sessionSettingMutations[sessionId];
      if (!latest || latest.sequence !== sequence) {
        return {
          response: { error: message },
          applied: false,
          stale: true
        };
      }
      const rollbackTouchSeq = settingsTouchSeq += 1;
      set((s) => ({
        sessions: s.sessions.map(
          (session) => session.id === sessionId ? mergeSessionSettingPatch(session, latest.authoritative) : session
        ),
        sessionSettingMutations: {
          ...s.sessionSettingMutations,
          [sessionId]: {
            ...latest,
            pending: false,
            patch: void 0,
            error: message
          }
        },
        _sessionSettingsTouchedSeq: {
          ...s._sessionSettingsTouchedSeq,
          [sessionId]: rollbackTouchSeq
        }
      }));
      throw error;
    }
  },
  updateSession: (id, data, preserveOnSnapshot = false) => {
    const touchSeq = wsTouchSeq += 1;
    set((s) => ({
      sessions: s.sessions.map(
        (session) => session.id === id ? preserveNewerSummary(
          session,
          mergeSessionSettingPatch(
            { ...session, ...data },
            s.sessionSettingMutations[id]?.pending ? s.sessionSettingMutations[id].patch : void 0
          )
        ) : session
      ),
      _sessionWsTouchedSeq: { ...s._sessionWsTouchedSeq, [id]: touchSeq },
      ...preserveOnSnapshot ? {
        _sessionEventPatches: {
          ...s._sessionEventPatches,
          [id]: { ...s._sessionEventPatches?.[id] ?? {}, ...data }
        }
      } : {}
    }));
  },
  applyResultToSession: (id, e) => {
    const status = e.status === "error" ? "error" : e.status === "cancelled" || e.cancelled ? "cancelled" : "done";
    const result = e.result;
    const touchSeq = localTouchSeq += 1;
    set((s) => {
      const sessions = s.sessions.map((x) => {
        if (x.id !== id) return x;
        const history = (x.history || []).slice();
        let historyTotal = x.historyTotal ?? history.length;
        if (typeof result === "string" && result.trim()) {
          const last = history[history.length - 1];
          if (!(last && last.role === "assistant" && last.content === result)) {
            history.push({ role: "assistant", content: result });
            historyTotal += 1;
          }
        }
        const bounded = history.length > 500 ? history.slice(-500) : history;
        return {
          ...x,
          history: bounded,
          historyTotal,
          // Card preview is summary-driven (lastMessage); keep it in sync with
          // the in-place append so the sidebar updates immediately.
          lastMessage: typeof result === "string" && result.trim() ? result.slice(0, 200) : x.lastMessage,
          lastResult: {
            status,
            result: result ?? "",
            timestamp: (/* @__PURE__ */ new Date()).toISOString()
          }
        };
      });
      return {
        sessions,
        _sessionLocalTouchedSeq: {
          ...s._sessionLocalTouchedSeq,
          [id]: touchSeq
        }
      };
    });
  },
  toggleMultiSelect: (initId) => {
    set((s) => {
      const isActive = !s.multiSelectMode;
      return {
        multiSelectMode: isActive,
        selectedIds: isActive && initId ? /* @__PURE__ */ new Set([initId]) : /* @__PURE__ */ new Set()
      };
    });
  },
  toggleSelection: (id) => {
    set((s) => {
      const next = new Set(s.selectedIds);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return { selectedIds: next };
    });
  },
  exitMultiSelect: () => {
    set({ multiSelectMode: false, selectedIds: /* @__PURE__ */ new Set() });
  },
  setRendering: (v) => {
    set({ rendering: v });
  },
  getUnread: () => {
    const { currentSessionId, sessionUnread } = get();
    if (!currentSessionId) return EMPTY_UNREAD_SET;
    return sessionUnread[currentSessionId] ?? EMPTY_UNREAD_SET;
  },
  markUnread: (sessionId, content) => {
    if (!sessionId) return;
    set((s) => {
      const previous = s.sessionUnread[sessionId] ?? EMPTY_UNREAD_SET;
      if (previous.has(content)) return {};
      const perSession = new Set(previous);
      perSession.add(content);
      return {
        sessionUnread: {
          ...s.sessionUnread,
          [sessionId]: perSession
        }
      };
    });
  },
  clearUnread: () => {
    const { currentSessionId } = get();
    if (!currentSessionId) return;
    set((s) => {
      const copy = { ...s.sessionUnread };
      copy[currentSessionId] = /* @__PURE__ */ new Set();
      return { sessionUnread: copy };
    });
  }
}));
if (typeof window !== "undefined" && new URLSearchParams(window.location.search).has("panE2E")) {
  window.__panSessionStore = useSessionStore;
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  useSessionStore
});
/*! Bundled license information:

react/cjs/react.production.js:
  (**
   * @license React
   * react.production.js
   *
   * Copyright (c) Meta Platforms, Inc. and affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   *)

react/cjs/react.development.js:
  (**
   * @license React
   * react.development.js
   *
   * Copyright (c) Meta Platforms, Inc. and affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   *)
*/
