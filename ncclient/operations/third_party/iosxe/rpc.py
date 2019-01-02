#
# IOS XE-specific implementation of an early draft of the IETF YANG
# Push drafts. This implementation may be used with IOS XE 16.6.1
# onwards where the platform advertises the capability
# `urn:ietf:params:netconf:capability:notification:1.1`.
#
# When IOS XE supports RFC-compliant versions of the IETF YANG Push
# functionality, this implementation will be retired.
#
from lxml import etree
from lxml.builder import ElementMaker
from ncclient.xml_ import *
from ncclient.operations.rpc import RPC
from ncclient.operations.rpc import RPCReply
from ncclient.operations.errors import AlreadyHasEventListener
from ncclient.transport import SessionListener
from dateutil.parser import parse as dateutil_parse
import logging


logger = logging.getLogger("ncclient.operations.rpc")

class SaveConfig(RPC):
    def request(self):
        node = new_ele_ns('save-config', 'http://cisco.com/yang/cisco-ia')
        return self._request(node)


class EstablishSubscriptionReply(RPCReply):

    """Establish Subscription Result RPCReply Class."""
    
    def _parsing_hook(self, root):
        self._result = None
        self._subscription_id = None
        self._message_id = root.get('message-id')
        if not self._errors:
            self._subscription_result = root.find(
                qualify("subscription-result", IETF_EVENT_NOTIFICATIONS_NS))
            self._subscription_id = root.find(
                qualify("subscription-id", IETF_EVENT_NOTIFICATIONS_NS))

    def _post_process(self, original_rpc):
        if self._subscription_id is not None:
            original_rpc.session.yang_push_listener.rekey_subscription_listener(
                self._message_id, int(self._subscription_id.text))
        else:
            original_rpc.session.yang_push_listener.remove_subscription_listener(
                        self._message_id)

    @property
    def subscription_result(self):
        "*subscription-result* element's text content"
        if not self._parsed:
            self.parse()
        return self._subscription_result.text

    @property
    def subscription_result_ele(self):
        "*subscription-result* element as an :class:`~xml.etree.ElementTree.Element`"
        if not self._parsed:
            self.parse()
        return self._subscription_result

    @property
    def subscription_result_xml(self):
        "*subscription-result* element as an XML string"
        if not self._parsed:
            self.parse()
        return to_xml(self._subscription_result)

    @property
    def subscription_id(self):
        "*subscription-id* element's integer value"
        if not self._parsed:
            self.parse()
        if self._subscription_id is not None:
            return int(self._subscription_id.text)
        else:
            return None

    @property
    def subscription_id_ele(self):
        "*subscription-id* element as an :class:`~xml.etree.ElementTree.Element`"
        if not self._parsed:
            self.parse()
        return self._subscription_id

    @property
    def subscription_id_xml(self):
        "*subscription-id* element as an XML string"
        if not self._parsed:
            self.parse()
        return to_xml(self._subscription_id)


class EstablishSubscription(RPC):
    
    "`establish-subscription` RPC"

    DEPENDS = ['urn:ietf:params:netconf:capability:notification:1.1']
    REPLY_CLS = EstablishSubscriptionReply
    
    def request(self, callback, errback,
                xpath=None,
                period=None, dampening_period=None,
                streamns=None, streamident=None):
        """Create a simple subscription for ietf-yang-push subscriptions.

        *callback* user-defined callback for notifications

        *errback* user-defined error handling callback

        *xpath* specifies the xpath-filter element

        *period* period for polling; on-change implied if not set

        *dampening_period* dampening period for change events

        *streamns* XML namespace for a non-default stream identifier

        *streamident* Non-default stream identity

        RPC currently supports only stream `yang-push`.
        """
        #
        # validate parameters
        #
        if xpath is None:
            raise YangPushError("Must have xpath")
        if period and dampening_period:
            raise YangPushError("Can only have one of period and dampening_period")
        if (period and streamident) or (dampening_period and streamident):
            raise YangPushError("Cannot combine custom stream with periodic or on-change")
        if (period is None) and (dampening_period is None) and (streamident is None):
            raise YangPushError("Must have at least one of period, dampening_period or streamident")
        if streamident and not streamns:
            raise YangPushError("Must specify namespace for custom stream")

        #
        # construct base rpc
        #
        rpc = new_ele_ns('establish-subscription', IETF_EVENT_NOTIFICATIONS_NS)

        #
        # Using ElementMaker to force the correct insertion of XML
        # namespaces for identity values.
        #
        if streamident:
            ele_maker = ElementMaker(namespace=IETF_EVENT_NOTIFICATIONS_NS,
                                     nsmap={'streamns': streamns})
            stream = ele_maker('stream')
            stream.text = 'streamns:{}'.format(streamident)
            rpc.append(stream)
            xpath_filter = sub_ele_ns(rpc, 'xpath-filter',
                                      IETF_YANG_PUSH_NS)
            xpath_filter.text = xpath
        else:
            ele_maker = ElementMaker(namespace=IETF_EVENT_NOTIFICATIONS_NS,
                                     nsmap={'yp': IETF_YANG_PUSH_NS})
            stream = ele_maker('stream')
            stream.text = 'yp:yang-push'
            rpc.append(stream)
            ele_xpath_filter = sub_ele_ns(rpc, 'xpath-filter',
                                          IETF_YANG_PUSH_NS)
            ele_xpath_filter.text = xpath
            if period:
                ele_period = sub_ele_ns(rpc, 'period',
                                        IETF_YANG_PUSH_NS)
                ele_period.text = str(period)
            else:
                ele_dampening_period = sub_ele_ns(rpc, 'dampening-period',
                                                  IETF_YANG_PUSH_NS)
                ele_dampening_period.text = str(dampening_period)


        # Install the listener if necessary, error if there is a
        # conflicting listener already installed, such as
        # NotificationHandler. Note that any new event listener type
        # will have to do something siimilar if they consume the same
        # events.
        if not hasattr(self.session, 'yang_push_listener'):
            if self.session.has_event_listener:
                raise AlreadyHasEventListener()
            else:
                self.session.yang_push_listener = YangPushListener()
                self.session.add_listener(self.session.yang_push_listener)
                self.session.has_event_listener = True

        # add the callbacks against the message id for now; will patch
        # that up in the reply
        self.session.yang_push_listener.add_subscription_listener(self._id, callback, errback)
            
        # Now process the request
        return self._request(rpc)


class DeleteSubscriptionReply(RPCReply):

    """Delete Subscription Result RPCReply Class."""
    
    def _parsing_hook(self, root):
        self._result = None
        self._subscription_id = None
        if not self._errors:
            self._subscription_result = root.find(
                qualify("subscription-result", IETF_EVENT_NOTIFICATIONS_NS))

    @property
    def subscription_result(self):
        "*subscription-result* element as an :class:`~xml.etree.ElementTree.Element`"
        if not self._parsed:
            self.parse()
        return self._subscription_result.text

    @property
    def subscription_result_ele(self):
        "*subscription-result* element as an :class:`~xml.etree.ElementTree.Element`"
        if not self._parsed:
            self.parse()
        return self._subscription_result

    @property
    def subscription_result_xml(self):
        "*subscription-result* element as an XML string"
        if not self._parsed:
            self.parse()
        return to_xml(self._subscription_result)


class DeleteSubscription(RPC):
    
    "`establish-subscription` RPC"

    DEPENDS = ['urn:ietf:params:netconf:capability:notification:1.1']
    REPLY_CLS = DeleteSubscriptionReply
    
    def request(self, subscription_id=None):
        """Create a simple subscription for ietf-yang-push subscriptions.

        *subscription_id* the id of the subscription to delete

        """
        #
        # validate parameters
        #
        if subscription_id is None:
            raise YangPushError("Must provide subscription_id")

        #
        # Construct request
        #
        node = new_ele_ns("delete-subscription", IETF_EVENT_NOTIFICATIONS_NS)
        to_delete = sub_ele_ns(node, "subscription-id", IETF_EVENT_NOTIFICATIONS_NS)
        to_delete.text = str(subscription_id)

        # remove subscription-id callbacks
        self.session.yang_push_listener.remove_subscription_listener(int(subscription_id))
        
        # Now process the request
        return self._request(node)


class YangPushNotificationType(object):

    """Simple enumeration of YANG push notification types."""
    
    UNKNOWN = 0
    PUSH_UPDATE = 1
    PUSH_CHANGE_UPDATE = 2

    @staticmethod
    def str_to_type(string):
        lookup = {
            "push-update": YangPushNotificationType.PUSH_UPDATE,
            "push-change-update": YangPushNotificationType.PUSH_CHANGE_UPDATE,
        }
        try:
            return lookup[string]
        except:
            raise Exception("Unknown YANG push notification type")


class YangPushNotification(object):

    """Represents a YANG Push `notification`."""
    
    def __init__(self, raw):
        self._raw = raw
        self._parsed = False
        self._root = None
        self._datastore = None
        self._event_time = None
        self._subscription_id = None
        self._type = None
        self._invalid = False
        
    def __repr__(self):
        return self._raw
    
    def parse(self):
        try:
            root = self._root = to_ele(self._raw)

            # extract eventTime
            event_time = root.find(qualify("eventTime", NETCONF_NOTIFICATION_NS))
            if event_time is not None:
                self._event_time = dateutil_parse(event_time.text)

            # determine type of event
            type = root.find(qualify("push-update", IETF_YANG_PUSH_NS))
            if type is not None:
                self._type = YangPushNotificationType.PUSH_UPDATE
                self._datastore = root.find(
                    './/%s' % qualify('datastore-contents-xml', IETF_YANG_PUSH_NS))
            else:
                type = root.find(qualify("push-change-update", IETF_YANG_PUSH_NS))
                if type is not None:
                    self._type = YangPushNotificationType.PUSH_CHANGE_UPDATE
                    self._datastore = root.find(
                        './/%s' % qualify('datastore-changes-xml', IETF_YANG_PUSH_NS))
                else:
                    self.type = YangPushNotificationType.UNKNOWN

            # extract subscription-id
            if type is not None:
                subscription_id = type.find(qualify("subscription-id", IETF_YANG_PUSH_NS))
                if subscription_id is not None:
                    self._subscription_id = int(subscription_id.text)

            # flag that we're parsed now
            self._parsed = True

        except Exception as e:
            self._invalid = True

    @property
    def invalid(self):
        if not self._parsed:
            self.parse()
        return self._invalid

    @property
    def xml(self):
        return self._raw

    @property
    def event_time(self):
        if not self._parsed:
            self.parse()
        return self._event_time

    @property
    def subscription_id(self):
        if not self._parsed:
            self.parse()
        return self._subscription_id

    @property
    def type(self):
        if not self._parsed:
            self.parse()
        return self._type

    @property
    def datastore_ele(self):
        if not self._parsed:
            self.parse()
        return self._datastore

    @property
    def datastore_xml(self):
        if not self._parsed:
            self.parse()
        return etree.tostring(self._datastore)

    @property
    def root_ele(self):
        if not self._parsed:
            self.parse()
        return self._root

    @property
    def root_xml(self):
        if not self._parsed:
            self.parse()
        return etree.tostring(self._root)


class YangPushListener(SessionListener):

    """Class extending :class:`Session` listeners, which are notified when
    a new RFC 5277 notification is received or an error occurs. Only a
    single instance of this class should be added to the listeners
    list, and then individual subscription callback should be added to
    the single listener.

    """
    def __init__(self):
        """Called by EstablishSubscription when a new NotificationListener is
        added to a session.  used to keep track of connection and
        subscription info in case connection gets dropped.
        """
        self.subscription_listeners = {}

        
    def add_subscription_listener(self, id, user_callback, user_errback):
        self.subscription_listeners[id] = (user_callback, user_errback)

        
    def rekey_subscription_listener(self, old_id, new_id):
        self.subscription_listeners[new_id] = self.subscription_listeners.pop(old_id)

        
    def remove_subscription_listener(self, id):
        self.subscription_listeners.pop(id)

        
    def callback(self, root, raw):
        """Called when a new RFC 5277 notification is received.

        The *root* argument allows the callback to determine whether
        the message is a notification.  Here, *root* is a tuple of
        *(tag, attributes)* where *tag* is the qualified name of the
        root element and *attributes* is a dictionary of its
        attributes (also qualified names).  *raw* will contain the xml
        notification as a string.
        """
        tag, attrs = root
        if tag != qualify("notification", NETCONF_NOTIFICATION_NS):
            # we just ignore any message not a notification
            return

        notif = YangPushNotification(raw)
        if notif.invalid:
            logger.error("Couldn't parse notification")
            return
        try:
            user_callback, _ = self.subscription_listeners[notif.subscription_id]
            user_callback(notif)
        except:
            logger.error("No callback for subscription_id=%d" % notif.subscription_id)


    def errback(self, ex):
        """Called when an error occurs. For now just handles a dropped connection.

        TODO: Needs fixed.

        :type ex: :exc:`Exception`
        """
        pass
        # self.user_errback(ex)
